# Contributing to GAARD

Thank you for your interest in improving GAARD. This project is open source,
but it is stewarded deliberately: maintainers keep control over roadmap,
architecture, security posture, licensing, and release decisions.

## Project Governance

GAARD is maintained by iTechnologie Sp. z o.o.

Maintainers have final authority over:

- what is accepted into the public project
- product direction, roadmap, and release timing
- public APIs, package boundaries, and compatibility promises
- security, compliance, and data-governance requirements
- whether a contribution belongs in the open-source project, a separate plugin,
  documentation, examples, or a private/enterprise product

Opening an issue or pull request does not create an obligation for maintainers
to accept, implement, support, or release the proposed change. Maintainers may
close issues or pull requests that are out of scope, inactive, too broad,
insufficiently tested, legally unclear, or not aligned with the project
direction.

GAARD may also have proprietary or enterprise editions. Contributions to this
public repository do not create any right to private source code, private
roadmaps, customer-specific work, commercial terms, trademarks, hosted
services, or enterprise features.

## Contribution Terms

The public GAARD project is licensed under the MIT License.

By submitting a contribution, you agree that:

- your contribution is provided under the MIT License unless a separate written
  agreement says otherwise
- iTechnologie Sp. z o.o. and downstream users may use, modify, distribute,
  sublicense, and incorporate your contribution in open-source and proprietary
  products or services under the project license, without additional approval,
  compensation, or attribution beyond required license notices
- you have the right to submit the contribution and it is not restricted by an
  employer, client, school, contract, patent obligation, or third-party license
- your contribution does not include confidential information, personal data,
  credentials, private keys, customer data, or proprietary code you are not
  allowed to publish
- your contribution does not knowingly infringe third-party rights

For substantial contributions, maintainers may require an additional Contributor
License Agreement, copyright assignment, or written confirmation of rights before
the pull request can be merged. If the requested confirmation is not provided,
the contribution may be declined.

## Sign-Off

Every commit must include a sign-off line:

```text
Signed-off-by: Your Real Name <you@example.com>
```

Use:

```bash
git commit -s
```

The sign-off confirms that you are allowed to submit the contribution under
these terms. Maintainers may ask you to fix missing sign-offs before review or
merge.

## What To Contribute

Good contributions are usually:

- focused bug fixes
- tests that reproduce or prevent real defects
- documentation that improves setup, operation, or integration
- connector improvements that do not weaken safety boundaries
- small, well-scoped features discussed with maintainers first
- examples that do not include sensitive, regulated, or customer-derived data

Please open an issue before starting large changes, new public APIs, new
dependencies, new service boundaries, authentication changes, data-governance
changes, or features that could belong in an enterprise edition.

## What Not To Contribute

Do not submit:

- secrets, tokens, API keys, private certificates, passwords, or database dumps
- real patient, customer, employee, financial, or other regulated data
- code copied from a source with unclear or incompatible licensing
- generated code, AI-generated code, or model output that you cannot review,
  explain, and license under these terms
- broad rewrites without prior maintainer agreement
- changes that bypass query validation, auditability, authentication, access
  controls, or safety limits
- dependencies under GPL, AGPL, SSPL, Commons Clause, source-available, or other
  restrictive terms without explicit maintainer approval

Prefer dependencies under permissive licenses such as MIT, BSD, ISC, or
Apache-2.0. Any new dependency must have a clear reason, an acceptable license,
and a small maintenance burden.

## Names And Trademarks

The MIT License covers copyright in the source code. It does not grant rights
to use GAARD names, logos, product names, domain names, company names, or other
branding except when reasonably necessary to describe the origin of an
unmodified copy of the project.

Maintainers control project branding and may require changes to names, logos,
metadata, documentation, screenshots, examples, or marketing language before a
contribution is merged.

## Development Setup

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-dev.txt
```

Create the local demo database when needed:

```bash
python examples/medical-poc/create_demo_db.py
```

Run the API:

```bash
python -m uvicorn gaard_api.main:app --reload --host 0.0.0.0 --port 8000
```

Run the community client in a second terminal:

```bash
python -m uvicorn gaard_client.main:app --reload --host 0.0.0.0 --port 8001
```

## Code Standards

GAARD is a Python monorepo with local editable packages.

Please keep changes:

- typed and compatible with the supported Python version
- small enough to review without guessing intent
- consistent with existing package boundaries
- explicit about safety, validation, and failure modes
- covered by focused tests when behavior changes
- documented when user-facing behavior, setup, or operations change

Avoid unrelated refactors in feature or bug-fix pull requests. If a cleanup is
needed, keep it separate or explain why it is necessary for the change.

## Tests And Checks

Run the relevant checks before opening a pull request:

```bash
pytest
ruff check .
mypy packages/gaard-core/src packages/gaard-connectors/src packages/gaard-llm/src packages/gaard-api/src packages/gaard-client/src
```

For focused work, run the smallest useful test target as well, for example:

```bash
pytest packages/gaard-core/tests
pytest packages/gaard-connectors/tests
pytest packages/gaard-llm/tests
pytest packages/gaard-api/tests
pytest packages/gaard-client/tests
```

If a check cannot be run, say so in the pull request and explain why.

## Pull Request Process

Before opening a pull request:

- rebase or update your branch against the current default branch
- keep the pull request focused on one problem
- include tests or explain why tests are not appropriate
- update documentation for user-visible changes
- describe risks, migrations, compatibility impact, and security impact
- call out any new dependency, generated artifact, or licensing concern

Maintainers may ask for changes, split a pull request, request more tests,
decline a change, or merge an alternative implementation. A pull request is not
accepted until it is merged by a maintainer.

## Security Issues

Do not report vulnerabilities in public issues or pull requests.

Send suspected security issues privately to the maintainers. Include:

- affected version or commit
- reproduction steps
- impact and affected components
- any known workaround

Do not include real secrets, production data, or third-party confidential
information in the report.

## Commit And Release Policy

Maintainers decide when and how changes are released. Merged code may be edited,
reverted, squashed, delayed, or excluded from a release if maintainers decide it
is necessary for quality, security, licensing, compatibility, or product
direction.

Version numbers, changelogs, release notes, package publication, container
images, and hosted services are controlled by maintainers.

## Communication

Be direct, specific, and respectful. Technical disagreement is welcome; personal
attacks, harassment, pressure tactics, entitlement to maintainer time, or
demands for commercial work in the public project are not.

The best way to get a change accepted is to make it small, clearly motivated,
well-tested, and aligned with GAARD's goal: governed AI access to relational
data with strong control over SQL generation, validation, execution, prompts,
connectors, and auditability.
