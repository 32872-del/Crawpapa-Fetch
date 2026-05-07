# Security Policy

## Supported Versions

Security fixes target the latest `main` branch.

## Responsible Use

Crawpapa-Fetch is intended for lawful analysis of public web pages and authorized data sources. It does not provide CAPTCHA cracking, credential theft, access-control bypass, or stealth abuse tooling.

Users are responsible for:

- Respecting robots.txt, site terms, and applicable law.
- Using only authorized cookies, accounts, APIs, and proxy infrastructure.
- Avoiding collection of private, sensitive, or personal data unless they have a lawful basis.
- Setting conservative rate limits for every target domain.

## Reporting a Vulnerability

Please open a private security advisory on GitHub if available, or create an issue with sensitive details omitted and request a private contact path.

Do not publish working exploit details for active vulnerabilities before maintainers have had a reasonable opportunity to respond.

## Secret Hygiene

Before opening a pull request or publishing a release:

```powershell
python tools/maintenance/secret_audit.py
```

The audit checks tracked files for common secrets, runtime artifacts, cookies, local paths, and generated outputs.

