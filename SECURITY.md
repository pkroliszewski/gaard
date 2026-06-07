# Security Policy

GAARD is intended for governed access to relational data, so security issues are
taken seriously. Please report suspected vulnerabilities privately and give
maintainers reasonable time to investigate before public disclosure.

## Supported Versions

Security fixes are handled for the current public `main` branch and the latest
public release, when releases are available.

Older versions, forks, private deployments, and modified builds may not receive
security fixes unless maintainers explicitly decide otherwise.

## Reporting A Vulnerability

Do not open a public issue or pull request for a suspected vulnerability.

If GitHub Security Advisories are enabled for the repository, use a private
security advisory. Otherwise, contact the maintainers privately through the
repository owner or other private maintainer channel.

Please include:

- affected version, commit, or deployment mode
- affected component or endpoint
- clear reproduction steps
- expected and actual behavior
- impact and likely severity
- any logs, screenshots, or proof-of-concept details that are safe to share
- known workarounds or mitigations, if any

Do not include real secrets, production data, customer data, patient data,
database dumps, access tokens, private keys, or third-party confidential
information.

## What Counts As A Security Issue

Examples of security issues include:

- authentication or authorization bypass
- access to data outside the intended datasource, tenant, user, or policy scope
- SQL validation bypasses that allow unsafe or unexpected database operations
- prompt, policy, or connector behavior that exposes sensitive data
- leakage of credentials, tokens, audit data, configuration, or internal errors
- unsafe defaults that could reasonably lead to data exposure
- dependency vulnerabilities that affect GAARD in a realistic configuration

General hardening ideas, feature requests, and low-impact configuration concerns
can be opened as normal issues when they do not reveal an exploitable problem.

## Responsible Testing

When researching or reporting a vulnerability:

- use your own test instance, local demo database, or data you are allowed to use
- avoid destructive testing, persistence, privilege escalation beyond what is
  needed to prove the issue, or attempts to access data that is not yours
- stop testing and report the issue if you encounter real sensitive data
- do not run automated scans against systems you do not own or have permission
  to test
- do not publicly disclose exploit details before maintainers have had a
  reasonable chance to investigate and release a fix

Good-faith reports that follow this policy are welcome. This policy does not
grant permission to attack third-party systems, violate laws, access private
data, or disrupt services.

## Maintainer Response

Maintainers will aim to:

- acknowledge the report within a reasonable time
- investigate and confirm the affected scope
- decide whether the issue requires a private fix, public advisory, release, or
  documentation update
- keep the reporter informed when practical
- credit the reporter if they want credit and if disclosure is appropriate

Maintainers may decline reports that are not reproducible, out of scope,
dependent on unsafe deployment choices, already known, not security-impacting,
or based on unrealistic assumptions.

## Disclosure

Public disclosure timing is controlled by maintainers. Once a fix or mitigation
is available, maintainers may publish a security advisory, release notes, patch,
or documentation update with an appropriate level of detail.

Please coordinate disclosure instead of publishing exploit details immediately.
