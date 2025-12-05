<p align="center">
  <img src="https://dummyimage.com/1400x320/0a1a2f/ffffff&text=SEA+PAY+PROCESSOR" alt="Sea Pay Processor Banner">
</p>

# Sea Pay Processor
Batch processor for NAVPERS 1070/613 sea pay documentation.

<p align="center">
  <img src="https://img.shields.io/badge/status-active-brightgreen">
  <img src="https://img.shields.io/badge/version-1.0.0-blue">
  <img src="https://img.shields.io/badge/docker-ready-green">
  <img src="https://img.shields.io/badge/python-3.11-yellow">
  <img src="https://img.shields.io/badge/license-private-lightgrey">
</p>

---

## Overview
Sea Pay Processor automatically extracts sea duty periods from certification sheets, validates them, normalizes ship names, detects duplicates, and generates NAVPERS 1070/613 PDFs. The system also produces summary files and strike-out PDFs for invalid or skipped events. The entire process is fully automated and containerized for consistent execution across environments.

---

## Docker Usage

### Run Example
```bash
docker run --rm \
  -e SEA_PAY_INPUT=/inputs \
  -e SEA_PAY_OUTPUT=/outputs \
  -e SEA_PAY_TEMPLATE=/templates/NAVPERS_1070_613_TEMPLATE.pdf \
  -v /mnt/user/SeaPayInput:/inputs \
  -v /mnt/user/SeaPayOutput:/outputs \
  -v /mnt/user/SeaPayTemplates:/templates \
  seapay-processor
```

### Environment Variables
| Variable | Description |
|---------|-------------|
| `SEA_PAY_INPUT` | Folder containing Sea Duty Certification Sheets |
| `SEA_PAY_OUTPUT` | Folder where PDFs and summaries are saved |
| `SEA_PAY_TEMPLATE` | NAVPERS 1070/613 template PDF |

---

## Output Structure

```
/outputs
│── VALID/
│     └── <rate>_<name>_<ship>_<dates>.pdf
│
│── INVALID/
│     └── strikeout_<original_file>.pdf
│
│── SUMMARY/
      └── <LAST_NAME>_SUMMARY.txt
```

### Summary Format
```
RATE LAST NAME

VALID SEA PAY PERIODS
- <Ship> <Start> to <End>

INVALID EVENTS
- <Reason> <Original Dates>

EVENTS FOLLOWED
- <List of processed events in order>
```

---

## Unraid Template Description (For Docker Setup)

Sea Pay Processor is an automated batch processor that reads Sea Duty Certification Sheets, validates sea duty periods, normalizes ship names, removes duplicates, and generates:

- Valid NAVPERS 1070/613 PDFs  
- Strike-out PDFs for invalid or skipped entries  
- Summary text files  

### Container Paths
- **/inputs** → Input PDFs  
- **/outputs** → Generated files  
- **/templates** → NAVPERS template  

### Recommended Unraid Paths  
- `/mnt/user/SeaPayInput`  
- `/mnt/user/SeaPayOutput`  
- `/mnt/user/SeaPayTemplates`

This container is designed for administrative teams requiring accurate, repeatable processing of sea duty paperwork.

---

## Maintainer
**Ryan Nivera**  
U.S. Navy Sonar Technician (Surface)

---

