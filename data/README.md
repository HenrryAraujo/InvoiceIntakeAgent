# Input data

The agent reads a mock inbound email and its PDF attachment from this folder:

- `Email.json` — the inbound email (Microsoft Graph message envelope).
- `Invoice.pdf` — the PDF attachment referenced by the email.

## Placing the files

These provided input files are **not committed** — they are gitignored so raw
email/PDF content never enters version control. Copy the files supplied with the
assignment into this folder before running:

```
data/
├── Email.json
└── Invoice.pdf
```

The CLI and API default to `data/Email.json` and resolve the attachment named in
the email (`Invoice.pdf`) from this same folder.
