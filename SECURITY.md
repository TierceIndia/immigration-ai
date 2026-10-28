# SECURITY.md

## DNS Rebinding Risk
The fetcher validates destination addresses at request time and caches DNS results briefly. However, DNS rebinding remains a residual risk: a hostname could resolve to a public IP during validation and later rebind to a private/internal IP before or during the request. 

**Mitigations in place:**
- DNS resolution is validated at every request/redirect hop
- Non-public addresses are blocked (localhost, 127.0.0.1, ::1, RFC1918, link-local, cloud metadata endpoints)
- Results cached briefly (60s) but re-validated
- `FETCH_VALIDATE_DESTINATION_DNS` can be disabled only in hermetic test environments (blocked in production)

**Recommendation:**
Run the fetcher on a network without access to postgres or other internal services. Use a network segment isolated from the database and other internal services to reduce blast radius if DNS rebinding occurs. Consider using a proxy with its own egress controls.

## Fetch Allowlist
By default, `FETCH_ALLOWED_DOMAINS` accepts any public host (`*`). For Australian immigration content, `.gov.au` is the recommended allowlist. Set `FETCH_ALLOWED_DOMAINS=.gov.au` in production for stricter control.
