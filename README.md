# Sea Pay Processor (Docker)

Batch processor for NAVPERS 1070/613 sea pay forms.

## Run (Docker)

```bash
docker run --rm \
  -e SEA_PAY_INPUT=/inputs \
  -e SEA_PAY_OUTPUT=/outputs \
  -e SEA_PAY_TEMPLATE=/templates/NAVPERS_1070_613_TEMPLATE.pdf \
  -v /mnt/user/SeaPayInput:/inputs \
  -v /mnt/user/SeaPayOutput:/outputs \
  -v /mnt/user/SeaPayTemplates:/templates \
  seapay-processor

Rebuild trigger
Force container rebuild
