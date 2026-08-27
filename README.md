# Hitachi Demo Guide Generator

Turns a recorded Microsoft Teams product demo into an automatically generated, Hitachi-branded navigation-guide PDF.

## Code overview

**`function_app.py`** — This is the main file that runs on Azure. It shows the web form, starts the job when someone submits a meeting link, and lets the browser check if the PDF is ready yet.

**`graph_client.py`** — This file talks to Microsoft to get the meeting recording and transcript. It logs in as the app itself (no person has to sign in), finds the right meeting from the link, and downloads the video and transcript files.

**`transcript_parser.py`** — This file takes the raw transcript file and turns it into a clean list of lines, each with a timestamp and the text that was said.

**`key_moments_extractor.py`** — This file sends the transcript to the AI and asks it to find the important moments in the call — the parts worth turning into a guide step. It also has the code that automatically retries if the AI is busy or overloaded.

**`screenshot_extractor.py`** — This file grabs a picture (screenshot) from the video at each important moment's timestamp.

**`pdf_report_generator.py`** — This file runs the whole process and builds the final PDF. It asks the AI to write the title and bullet points for each screenshot, then puts everything together into pages.

**`blob_storage.py`** — This file saves the finished PDF to Azure storage, keeps track of whether a job is still running or done, and creates the secure link used to download the PDF.
