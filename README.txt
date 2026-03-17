YouTube Transcript Pipeline
===========================

This project handles the transcript-side pipeline only.
It scrapes YouTube videos, stores transcript datasets, optionally simplifies those transcripts,
and builds transcript-derived dossiers.

Main modes
----------
- scrape
- repair
- batch_repair
- simplify
- batch_simplify
- dossier
- batch_dossiers
- audit

Data flow
---------
1. scrape
   Writes raw transcript datasets for creators and search results.

2. simplify (optional)
   Creates simplified transcripts for one creator.
   This removes obvious filler and obvious channel-promo noise while trying to preserve every
   substantive statement.

3. dossier
   Builds a dossier for one creator from either:
   - raw transcripts
   - simplified transcripts

4. repair
   Rebuilds missing summary/index files, optionally simplifies when needed, reruns dossier output,
   and then audits the creator folder.

5. audit
   Checks completeness and consistency for the selected source layer.

How creator selection works
---------------------------
Set these in config/settings.txt:
- DATASET_ROOT
- ACTIVE_CREATOR
- DOSSIER_SOURCE

Example:
DATASET_ROOT=trading_dataset
ACTIVE_CREATOR=TradingLabOfficial
DOSSIER_SOURCE=raw

If DOSSIER_SOURCE=simple, simplified transcripts must exist first.
If they do not exist, dossier mode stops and tells you to simplify first.

Folders
-------
Raw transcripts:
- <creator>/individual_video_scripts/

Simplified transcripts:
- <creator>/individual_video_scripts_simple/

Raw analysis:
- <creator>/_analysis/

Simplified analysis:
- <creator>/_analysis_simple/

Important files
---------------
Raw dataset files:
- video_index.json
- video_index.csv
- channel_alltranscripts.txt
- channel_allvideostranscribed.txt

Simplified dataset files:
- video_index_simple.json
- video_index_simple.csv
- channel_alltranscripts_simple.txt
- channel_allvideostranscribed_simple.txt

Per-source analysis files:
- incremental_state.json
- notes_manifest.json
- dossier_manifest.json
- consistency_audit.json

Windows helpers
---------------
- run_scrape.bat
- run_simplify.bat
- run_batch_simplify.bat
- run_dossier.bat
- run_batch_dossiers.bat
- run_audit.bat
- run_repair.bat
- run_batch_repair.bat

Recommended usage
-----------------
Single creator, raw dossier:
1. Set ACTIVE_CREATOR and DOSSIER_SOURCE=raw in config/settings.txt
2. run_scrape.bat
3. run_repair.bat or run_dossier.bat
4. run_audit.bat

Single creator, simplified dossier:
1. Set ACTIVE_CREATOR and DOSSIER_SOURCE=simple in config/settings.txt
2. run_scrape.bat
3. run_simplify.bat
4. run_dossier.bat
5. run_audit.bat

Batch flow:
1. run_scrape.bat
2. run_batch_simplify.bat (optional)
3. run_batch_repair.bat or run_batch_dossiers.bat

Important note about simplification
-----------------------------------
Simplification is conservative by design.
It targets obvious filler and obvious promo lines near the beginning/end of transcripts.
It is not intended to paraphrase, summarize, or reinterpret the content.

Prompt files
------------
Prompt files were intentionally not changed.
