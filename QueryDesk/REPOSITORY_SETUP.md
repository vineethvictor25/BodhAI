# QueryDesk repository setup

The application lives in `QueryDesk/langgraph_impl`. Runtime environments, credentials,
provider approvals, account/session databases and generated datasets are local files,
excluded from Git. Generated video files are also excluded.

## Install a fresh checkout on Windows

Requires Python 3.11 and Node.js compatible with the locked frontend dependencies.

```powershell
cd C:\BodhAI\QueryDesk
.\langgraph_impl\setup-ui.ps1
```

Setup creates a new virtual environment, installs locked dependencies, builds the
frontend, copies safe example configuration when absent, and generates the small
retail sample. Supply your own API key in `langgraph_impl/.env`; configure the model
endpoint and ID, then review/approve the matching profile in `model_policy.json`.
Provider approval is an explicit operator decision. Never commit these local files.

For a new installation, configure a private `AUTH_SETUP_TOKEN` of 32–256 characters
in `.env` for first-administrator setup, as described in the security guide.

Generate/register the larger synthetic analytics lab:

```powershell
cd .\langgraph_impl
.\.venv\Scripts\python.exe -m langgraph_impl.analytics_demo --register-local
cd ..
.\langgraph_impl\start-ui.ps1 -OpenBrowser
```

The local mirror is a combined SQLite source. Native same-instance cross-database
joins require deploying the generated SQL Server seed and merging its source/policy
snippets. See [the analytics lab guide](langgraph_impl/ANALYTICS_LAB.md).

## Existing laptop copy

The copy at `C:\BodhAI\QueryDesk` retains the original private configuration and
data locally; embedded analytics configuration paths have been relocated. Its
Python environment and Node dependencies are recreated rather than copied.
These private local files are not included in the branch or available to clones.
Stop the old UI before starting the copied installation on the same ports.
The original `C:\NLQ to SQL` folder remains unchanged.

## Checks

```powershell
cd .\langgraph_impl
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest discover -s tests
cd .\web
npm test
npm run build
npm run test:e2e
```

Backend and browser exercises use synthetic fixtures. They do not certify live
model-provider access or connectivity to an operator's remote warehouse.
