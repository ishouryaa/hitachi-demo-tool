"""
function_app.py

Azure Functions entry point for the Hitachi Demo Tool. Wraps the existing
pipeline (graph_client -> key_moments_extractor -> screenshot_extractor ->
pdf_report_generator) behind an async job flow, plus a simple HTML trigger
form so anyone in the org can use it without touching a terminal.

Why async: the full pipeline (Graph fetch + several Gemini calls + PDF
render) can run well past Azure's ~230s HTTP response limit on longer demo
recordings, which used to fail with a generic "Server Error". So instead of
doing everything inside one blocking HTTP call, the flow is:

  1. run_demo_capture (HTTP, fast) - validates input, kicks off the pipeline
     on a BACKGROUND THREAD on this same instance, and returns a job_id
     immediately (well under the 230s limit). The thread keeps running after
     the response is sent.
  2. get_job_status (HTTP, fast) - the form polls this every few seconds.
     The steady polling also keeps this instance warm, which is what keeps
     the background thread alive until it finishes. Once status is "done" it
     returns a short-lived direct download URL for the PDF (no login/Azure
     access needed to fetch it).

Why a background thread instead of a queue-triggered function: on the Flex
Consumption plan, HTTP and queue-trigger functions scale on SEPARATE
instances, and the queue instance frequently fails to scale up from zero -
so queued jobs just sit there unprocessed. Running the work on the already-
warm HTTP instance sidesteps that entirely and needs no extra Azure config.
Trade-off: if every browser stops polling AND the platform recycles this
instance mid-job, the thread dies (status stays "running"). For this tool -
one person watching the page while it runs - that's fine. If it ever becomes
a problem, set the Function App's "Always Ready instances" to 1.

End-user experience is unchanged: paste link, click Generate, wait, get the
PDF (or a clear error) - the polling just happens invisibly in the page.

Local testing:
    func start
    curl -X POST "http://localhost:7071/api/run-demo-capture" \
         -H "Content-Type: application/json" \
         -d '{"join_url": "https://teams.microsoft.com/...", "organizer_email": "someone@company.com"}'
    curl "http://localhost:7071/api/job-status/<job_id>"

Deployed:
    Open https://<function-app-name>.azurewebsites.net/api/ in a browser for the form.
"""

import json
import logging
import os
import tempfile
import threading
import uuid

import azure.functions as func

from blob_storage import generate_download_url, read_job_status, upload_pdf, write_job_status
from graph_client import fetch_meeting_artifacts
from pdf_report_generator import generate_navigation_pdf

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

FORM_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Hitachi Demo Guide Generator</title>
<style>
  body {{ font-family: Helvetica, Arial, sans-serif; max-width: 480px; margin: 60px auto; color: #282828; }}
  h1 {{ color: #E60028; font-size: 22px; }}
  label {{ display: block; margin-top: 16px; font-weight: bold; font-size: 14px; }}
  input {{ width: 100%; padding: 8px; margin-top: 4px; box-sizing: border-box; font-size: 14px; }}
  button {{ margin-top: 20px; background: #E60028; color: white; border: none; padding: 10px 18px;
            font-size: 15px; cursor: pointer; }}
  button:disabled {{ background: #999; cursor: default; }}
  #status {{ margin-top: 16px; font-size: 14px; color: #555; }}
</style>
</head>
<body>
  <h1>Hitachi Demo Guide Generator</h1>
  <form id="form">
    <label for="join_url">Teams meeting join link</label>
    <input type="text" id="join_url" required placeholder="https://teams.microsoft.com/...">

    <label for="organizer_email">Meeting organizer's email</label>
    <input type="email" id="organizer_email" required placeholder="you@company.com">

    <button type="submit" id="submit_btn">Generate PDF</button>
  </form>
  <p id="status"></p>

  <script>
    const form = document.getElementById('form');
    const status = document.getElementById('status');
    const submitBtn = document.getElementById('submit_btn');

    const POLL_INTERVAL_MS = 4000;
    const MAX_POLLS = 150; // ~10 minutes

    async function pollStatus(jobId) {{
      for (let i = 0; i < MAX_POLLS; i++) {{
        await new Promise(r => setTimeout(r, POLL_INTERVAL_MS));

        let resp;
        try {{
          resp = await fetch(`/api/job-status/${{jobId}}`);
        }} catch (err) {{
          continue; // transient network hiccup - just try again next tick
        }}

        if (!resp.ok) {{
          const body = await resp.text();
          status.textContent = 'Error checking status (HTTP ' + resp.status + '): ' + (body || '(no details)');
          submitBtn.disabled = false;
          return;
        }}

        const data = await resp.json();
        if (data.status === 'done') {{
          status.textContent = 'Done - starting download...';
          window.location.href = data.download_url;
          status.textContent = 'Done - check your downloads.';
          submitBtn.disabled = false;
          return;
        }}
        if (data.status === 'error') {{
          status.textContent = 'Error: ' + (data.error || 'Something went wrong.');
          submitBtn.disabled = false;
          return;
        }}
        status.textContent = 'Working (' + data.status + ') - this can take a few minutes...';
      }}
      status.textContent = 'Still working after a while - check back in a bit, or try again.';
      submitBtn.disabled = false;
    }}

    form.addEventListener('submit', async (e) => {{
      e.preventDefault();
      submitBtn.disabled = true;
      status.textContent = 'Starting...';

      const join_url = document.getElementById('join_url').value;
      const organizer_email = document.getElementById('organizer_email').value;

      try {{
        const resp = await fetch('/api/run-demo-capture?code={function_key}', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ join_url, organizer_email }})
        }});

        if (!resp.ok) {{
          status.textContent = 'Error: ' + await resp.text();
          submitBtn.disabled = false;
          return;
        }}

        const data = await resp.json();
        status.textContent = 'Working - this can take a few minutes...';
        await pollStatus(data.job_id);
      }} catch (err) {{
        status.textContent = 'Error: ' + err;
        submitBtn.disabled = false;
      }}
    }});
  </script>
</body>
</html>
"""


@app.route(route="", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def serve_form(req: func.HttpRequest) -> func.HttpResponse:
    # The page itself is anonymous so anyone with the link can open it, but the
    # run-demo-capture and job-status calls it makes still need a key - read
    # from an app setting so the real key never lives in source control. This
    # must be a HOST key (not a function-specific key), since the page now
    # calls two different HTTP functions with the same code param.
    function_key = os.environ.get("RUN_DEMO_CAPTURE_KEY", "")
    html = FORM_HTML.format(function_key=function_key)
    return func.HttpResponse(html, mimetype="text/html")


@app.route(route="run-demo-capture", methods=["POST"])
def run_demo_capture(req: func.HttpRequest) -> func.HttpResponse:
    """Fast entry point: validates input, starts the real work on a
    background thread, and returns a job_id right away. Does not touch
    Graph/Gemini/the PDF on this request, so it can never hit the 230s limit.
    """
    join_url = req.params.get("join_url")
    organizer_email = req.params.get("organizer_email")
    if not join_url or not organizer_email:
        try:
            body = req.get_json()
        except ValueError:
            body = {}
        join_url = join_url or (body.get("join_url") if body else None)
        organizer_email = organizer_email or (body.get("organizer_email") if body else None)

    if not join_url:
        return func.HttpResponse(
            'Missing "join_url". Send it as a query param or in a JSON body: '
            '{"join_url": "...", "organizer_email": "..."}',
            status_code=400,
        )
    if not organizer_email:
        return func.HttpResponse(
            'Missing "organizer_email" (the meeting organizer\'s email/UPN).',
            status_code=400,
        )

    job_id = str(uuid.uuid4())
    try:
        write_job_status(job_id, "queued")
    except Exception as e:
        logging.exception("Failed to write initial job status")
        return func.HttpResponse(f"Error starting job: {e}", status_code=500)

    # Do the heavy pipeline on a background thread so this HTTP call returns
    # immediately. daemon=True so it never blocks host shutdown. The thread
    # writes its own status updates (running -> done/error).
    thread = threading.Thread(
        target=_run_job, args=(job_id, join_url, organizer_email), daemon=True
    )
    thread.start()

    return func.HttpResponse(
        json.dumps({"job_id": job_id, "status": "queued"}),
        status_code=202,
        mimetype="application/json",
    )


def _run_job(job_id: str, join_url: str, organizer_email: str) -> None:
    """Background worker: the actual fetch + Gemini + PDF pipeline. Runs on a
    thread on the warm HTTP instance (see module docstring). Always writes a
    terminal status ("done" or "error") so the polling page never hangs.
    """
    try:
        write_job_status(job_id, "running")

        gemini_key = os.environ.get("GEMINIAPIKEY", "")
        logging.info("Using Gemini key ending in: ...%s", gemini_key[-6:] if gemini_key else "(not set)")

        with tempfile.TemporaryDirectory() as work_dir:
            logging.info("Fetching meeting artifacts for %s (organizer: %s)", join_url, organizer_email)
            try:
                artifacts = fetch_meeting_artifacts(join_url, organizer_email, out_dir=work_dir)
            except Exception as e:
                logging.exception("Failed to fetch meeting artifacts")
                write_job_status(job_id, "error", error=f"Error fetching meeting: {e}")
                return

            if not artifacts["recording_path"] or not artifacts["transcript_path"]:
                write_job_status(
                    job_id,
                    "error",
                    error=(
                        "Recording or transcript not available yet for this meeting "
                        "(the call may still be processing - try again in a few minutes)."
                    ),
                )
                return

            pdf_path = os.path.join(work_dir, "navigation_guide.pdf")
            logging.info("Generating navigation PDF")
            try:
                pdf_result = generate_navigation_pdf(
                    artifacts["recording_path"],
                    artifacts["transcript_path"],
                    output_pdf=pdf_path,
                    screenshots_dir=os.path.join(work_dir, "screenshots"),
                )
            except Exception as e:
                logging.exception("Failed to generate PDF")
                write_job_status(job_id, "error", error=f"Error generating PDF: {e}")
                return
            guide_title = pdf_result.get("title") or artifacts.get("subject") or ""

            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

            try:
                blob_name = upload_pdf(
                    pdf_bytes, subject=artifacts.get("subject"), meeting_id=artifacts.get("meeting_id")
                )
            except Exception as e:
                logging.exception("Failed to upload PDF to blob storage")
                write_job_status(job_id, "error", error=f"Error saving PDF: {e}")
                return

        write_job_status(job_id, "done", blob_name=blob_name, guide_title=guide_title)
    except Exception as e:
        # Last-resort catch so a job never silently dies with status stuck.
        logging.exception("Unexpected error in background job")
        try:
            write_job_status(job_id, "error", error=f"Unexpected error: {e}")
        except Exception:
            logging.exception("Also failed to record error status")


@app.route(route="job-status/{job_id}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_job_status(req: func.HttpRequest) -> func.HttpResponse:
    # Anonymous on purpose: the job_id is an unguessable random UUID that only
    # ever lives in the requesting browser, so it acts as the bearer token.
    # This avoids the host-key/function-key confusion that broke polling, and
    # lets the page check status without needing a key that works across
    # multiple functions.
    job_id = req.route_params.get("job_id")
    status = read_job_status(job_id)
    if status is None:
        return func.HttpResponse(
            json.dumps({"status": "error", "error": "Unknown job_id."}),
            status_code=404,
            mimetype="application/json",
        )

    result = {"status": status["status"]}
    if status["status"] == "error":
        result["error"] = status.get("error", "Unknown error.")
    elif status["status"] == "done":
        try:
            result["download_url"] = generate_download_url(status["blob_name"], download_filename=status.get("guide_title"))
        except Exception as e:
            logging.exception("Failed to generate download URL")
            result = {"status": "error", "error": f"Job finished but couldn't create a download link: {e}"}

    return func.HttpResponse(json.dumps(result), status_code=200, mimetype="application/json")
