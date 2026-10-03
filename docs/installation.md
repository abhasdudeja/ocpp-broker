# Installation Guide

## Requirements

- **Python 3.10 or newer** on Windows, macOS or Linux
- `pip`
- **MongoDB** (optional): only if you want data persisted, durable transaction ids and tags that survive a restart. The broker connects to an existing MongoDB; it does not install one. See [MongoDB Integration](mongodb-integration.md).

Runtime dependencies (installed automatically): `ocpp` (>=1.0.0), `fastapi` (>=0.111), `uvicorn`, `websockets`, `pyyaml`, `motor`, `pymongo` and `python-dotenv`. `pyproject.toml` is the source of truth; `requirements.txt` mirrors it.

## Install

### From PyPI

```bash
pip install ocpp-broker
```

### From a checkout

```bash
git clone <your repository URL>
cd ocpp-broker
pip install -e .
```

`pip install -r requirements.txt` installs the same runtime dependencies without the package itself. It is not enough to run the broker unless the `src` directory is on `PYTHONPATH`; prefer `pip install -e .`.

Use a virtual environment if you do not want the packages installed globally:

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e .
```

There is no official Docker image, `Dockerfile` or `docker-compose.yml` in this repository. The [Deployment Guide](deployment.md) shows how to run the broker as a service.

## Check the installation

```bash
ocpp-broker-server --help
python -c "import ocpp_broker; print(ocpp_broker.__version__)"
pip show ocpp-broker
```

`ocpp-broker-server` is equivalent to `python -m ocpp_broker.server`. Its only option is `-c` / `--config`; there is no `--version`, `--dry-run` or `--verbose` flag.

To check a configuration file without starting the server:

```bash
python -c "from ocpp_broker.config import load_config; load_config('config.yaml')"
```

## First run

1. Create a `config.yaml` (see the [Quick Start](quick-start.md) or the example in the repository root).
2. Set the REST API key, otherwise REST calls return `503`:

   ```bash
   export OCPP_BROKER_API_KEY=change-me
   ```

3. Start the broker:

   ```bash
   ocpp-broker-server -c config.yaml
   ```

4. Check it:

   ```bash
   curl http://localhost:8765/health
   ```

   ```json
   {"status":"ok"}
   ```

The broker looks for `config.yaml` in the current directory when `-c` is not given. Everything (chargers, REST API, Swagger UI at `/docs`) is served on `broker.port`, default 8765.

## Development installation

```bash
pip install -e ".[tests,lint]"

pytest                      # the test suite
ruff check src tests        # lint
mypy                        # type check
```

The `tests` extra adds `pytest`, `pytest-asyncio`, `httpx` and `websockets>=14`; the `lint` extra adds `ruff`, `mypy` and `types-PyYAML`. The same three commands run in CI.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `Address already in use` | Another process uses the port. Change `broker.port` in `config.yaml` (or set `BROKER_PORT`). |
| `Permission denied` on startup | Ports below 1024 need elevated privileges. Use a higher port, or put a reverse proxy in front. |
| `No module named 'ocpp_broker'` | The package is not installed in the interpreter you are using. Run `pip install -e .` with that interpreter. |
| `ImportError` for `motor` or `pymongo` | You installed from an old `requirements.txt` or by hand. Reinstall with `pip install -e .`. |
| Every charger is refused with close code `4002` | The configuration file was not found (use `-c`), so the broker started with no organizations. |

More in [Troubleshooting](troubleshooting.md).

## Next steps

- [Quick Start](quick-start.md): connect a charger in five minutes
- [Configuration Guide](configuration.md)
- [API Reference](api-reference.md)
