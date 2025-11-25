# PG13 Sea Pay Generator

This project provides a Dockerized web app that:
- Accepts a SEA DUTY CERTIFICATION SHEET PDF
- Extracts Sailor names and events
- Groups events by ship
- Skips MITE events
- Generates NAVPERS 1070/613 PG-13 PDFs per ship per Sailor
- Returns a ZIP file with all PG-13s, organized by Sailor

## Quick Start (Docker)

```bash
docker compose up -d --build
```

Then browse to:

```
http://<UNRAID-IP>:8080
```

Upload a SEA DUTY CERTIFICATION SHEET PDF and download the generated
`pg13_output.zip`.
