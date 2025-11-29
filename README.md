# ATGSD Sea Pay Processor (Unraid Docker)

Processes ATGSD sea pay PDFs into NAVPERS 1070/613 pages with:
- OCR (Tesseract)
- Ship/date grouping
- Automatic rate lookup from `atgsd_n811.csv`
- Master merged packet with bookmarks

## Folder mappings (recommended on Unraid)

- `/data`      → input PDFs
- `/templates` → NAVPERS_1070_613_TEMPLATE.pdf
- `/config`    → `atgsd_n811.csv`
- `/output`    → generated PDFs + `MASTER_SEA_PAY_PACKET.pdf`

## Web UI

Once the container is running:

- `http://UNRAID-IP:8092` (if you map host 8092 → container 8080)

You can adjust:
- Data directory
- Template PDF path
- Rate CSV path
- Output directory

Then click **RUN PROCESSOR** and watch logs in real time.

