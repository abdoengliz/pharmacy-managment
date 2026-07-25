# v5.6.0-dev — Sales Module Enterprise (Phase 1)

## Included

- Customer master data with automatic customer numbering.
- Customer list and creation interface.
- Sales invoice draft workflow.
- Multi-line invoice items with quantity, price, discount and tax calculations.
- Automatic invoice numbering by year.
- Sales invoice list and detail pages.
- Event Bus publication for customer creation and invoice draft creation.
- Automatic Activity Timeline entries and Audit integration.
- Database schema upgraded to 5.6.

## Scope note

This phase intentionally saves invoices as `DRAFT`. Posting, stock deduction, payment settlement, returns and approval thresholds are planned for the next sales phases.

## Quality

- Python compilation completed successfully.
- 15 automated tests passed.
