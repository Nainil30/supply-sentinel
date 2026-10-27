# Security policy

Supply Sentinel is a portfolio project. It runs on synthetic data for a fictional company and ships no production secrets.

## Reporting

Open a private security advisory on GitHub (Security tab, "Report a vulnerability"), or email the repository owner through the address on their GitHub profile. Please do not file public issues for vulnerabilities. Expect an acknowledgement within a week; this is a personal project, so there is no SLA.

## Scope

In scope: prompt-injection bypasses of the untrusted-text scanner, ways the agent could approve or send something without a human, secrets leaking into cassettes, ledgers or images, and unsafe defaults in the Docker setup.

Out of scope: the synthetic data itself, findings that need a real API key you supplied, and denial of service against a local demo.

## Secrets

Secrets come only from environment variables. `.env` is gitignored; `.env.example` lists the names. Cassettes store messages, never headers or keys, and a test scans them for token-like strings. See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).
