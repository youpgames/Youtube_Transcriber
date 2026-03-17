# Security and Privacy

## What this project may process
This project can scrape publicly available YouTube metadata and transcripts, generate cleaned transcript files, and create research dossiers from that data.

## What should NOT be committed
Do not upload any of the following to GitHub:

- scraped datasets
- transcript outputs
- simplified transcript outputs
- generated dossiers or PDFs
- logs containing local file paths
- cookies, tokens, API keys, or `.env` files
- any personal notes or documents generated from private data

## Personal data guidance
Even when source videos are public, generated files may still contain:

- local usernames in file paths
- creator/channel identifiers you may not want to publish
- timestamps or notes tied to your personal workflow

Review generated files before publishing.

## Safe publishing checklist
Before pushing the repo:

1. Confirm `.gitignore` is active.
2. Remove dataset folders such as `trading_dataset/`, `dataset/`, or `data/`.
3. Remove generated folders such as `*_analysis/`, `*_notes/`, `*_raw/`, and reports.
4. Search the repo for: `token`, `secret`, `password`, `cookie`, `C:\\Users\\`, and your own username.
5. Verify `config/` contains only safe templates, not private runtime data.

## Responsible use
Users are responsible for complying with platform terms, copyright rules, and local laws when scraping, storing, or processing transcripts.
