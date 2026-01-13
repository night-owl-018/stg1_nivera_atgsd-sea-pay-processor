<p align="center">
  <img src="https://dummyimage.com/1400x320/0a1a2f/ffffff&text=SEA+PAY+PROCESSOR" alt="Sea Pay Processor Banner">
</p>

# ATGSD Sea Pay Processor
### Developed by STG1(SW) Ryan Nivera © 2026
Batch processor for NAVPERS 1070/613 sea pay documentation.


<p align="center">
  <img src="https://img.shields.io/badge/status-active-brightgreen">
  <img src="https://img.shields.io/badge/version-1.0.0-blue">
  <img src="https://img.shields.io/badge/docker-ready-green">
  <img src="https://img.shields.io/badge/python-3.11-yellow">
  <img src="https://img.shields.io/badge/license-private-lightgrey">
</p>

---

# NOTE FOR UNRAID USERS
The running application uses the following fixed container paths:


These paths are automatically created and do not use the older environment variables  
(`SEA_PAY_INPUT`, `SEA_PAY_OUTPUT`, `SEA_PAY_TEMPLATE`) found in the legacy scripts.

When mapping Unraid container paths, map your host folders directly to these container paths.

---

## Overview
Sea Pay Processor automatically extracts sea duty periods from certification sheets, validates them, normalizes ship names, detects duplicates, and generates NAVPERS 1070/613 PDFs. The system also produces summary files and strike-out PDFs for invalid or skipped events. The entire process is fully automated and containerized for consistent execution across environments.

---

## Docker Usage

### Run Example
```bash
docker run --rm \
  -v /mnt/user/SeaPayInput:/data \
  -v /mnt/user/SeaPayOutput:/output \
  -v /mnt/user/SeaPayTemplates:/templates \
  -v /mnt/user/SeaPayConfig:/config \
  seapay-processor

Ryan Nivera
U.S. Navy Sonar Technician (Surface)


