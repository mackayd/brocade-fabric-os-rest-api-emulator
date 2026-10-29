# Brocade Fabric OS REST API Emulator

A dual-personality Fabric OS REST API emulator (v9/v10) for testing monitoring integrations without physical SAN switches. It provides synthetic Dell Connectrix DS-6620B/Brocade G620 and DS-7720B/Brocade G720 endpoints, session authentication, inventory and performance data, and repeatable fault/recovery scenarios.

The emulator was developed for the [Brocade Fabric OS Community Management Pack for VMware Aria Operations](https://github.com/mackayd/aria-operations-Brocade-Fabric-OS-Community-management-pack), but its HTTP API can also support other integration and parser tests.

> This is an independent community test tool. It is not a full Fabric OS implementation and is not an official Dell, Broadcom, or VMware product. All identities, WWNs, serial numbers, endpoints, and measurements are synthetic. Do not use it as a substitute for validation against the intended physical switches and firmware.

## Emulator personalities

| Personality | Synthetic platform | Switch type | Firmware identity | Data API | Control API |
| --- | --- | --- | --- | --- | --- |
| `g620` | DS-6620B / G620 | `162.0` | `v9.2.2c1` | `http://127.0.0.1:8620` | `http://127.0.0.1:18620` |
| `g720` | DS-7720B / G720 | `181.5` | `v10.0.1` | `http://127.0.0.1:8720` | `http://127.0.0.1:18720` |

Each profile exposes 64 logical ports and model-specific port speeds, media types, fans, power supplies, temperature sensors, transceivers, connected endpoints, and MAPS data. The two switches are members of one synthetic mixed-model fabric with an inter-switch link.

## Requirements

- Python 3.10 or later is recommended.
- No third-party Python packages are required.
- Windows PowerShell 5.1 or PowerShell 7 can use the supplied start, stop, and scenario helpers.
- Linux and macOS users can run the Python module directly.

## Quick start on Windows

Clone or download the repository, open PowerShell in its root, and run:

```powershell
$env:BROCADE_EMULATOR_USERNAME = 'monitor'
$env:BROCADE_EMULATOR_PASSWORD = 'choose-a-lab-password'
.\tools\Start-BrocadeEmulators.ps1
```

The helper starts both personalities as hidden Python processes and displays their process IDs, data URLs, control URLs, and log paths. Runtime state and logs are written to `emulator/.runtime/` and excluded from Git.

Verify a data endpoint:

```powershell
curl.exe -u "monitor:$env:BROCADE_EMULATOR_PASSWORD" `
  http://127.0.0.1:8620/rest/running/brocade-fibrechannel-switch/fibrechannel-switch
```

Stop both emulators with:

```powershell
.\tools\Stop-BrocadeEmulators.ps1
```

If credential environment variables are not set, the local development defaults are username `monitor` and password `emulator-only`. Set a different password before exposing the data API beyond loopback. The supplied `emulator/.env.example` is a reference template; the emulator does not load `.env` files automatically.

## Run on Linux or macOS

Run each personality in a separate terminal from the repository root:

```bash
export BROCADE_EMULATOR_USERNAME='monitor'
export BROCADE_EMULATOR_PASSWORD='choose-a-lab-password'

python -m emulator.brocade_fos_emulator \
  --profile emulator/profiles/ds6620b-g620/profile.json \
  --host 127.0.0.1 --port 8620 --control-port 18620
```

```bash
python -m emulator.brocade_fos_emulator \
  --profile emulator/profiles/ds7720b-g720/profile.json \
  --host 127.0.0.1 --port 8720 --control-port 18720
```

Stop each foreground process with `Ctrl+C`.

## Connect a remote test client

The Windows helper binds the data APIs to loopback by default. To make them reachable from an authorized lab client, bind them to all local interfaces:

```powershell
.\tools\Start-BrocadeEmulators.ps1 -BindAddress 0.0.0.0
```

Permit only the intended client or collector through the host firewall. For example, from an elevated PowerShell session on Windows:

```powershell
New-NetFirewallRule -DisplayName 'Brocade FOS emulator data APIs' `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8620,8720 `
  -RemoteAddress '<collector-ip-address>'
```

The scenario-control APIs remain bound to `127.0.0.1`; they are not remotely exposed by the helper.

For the Brocade Fabric OS Aria Operations management pack, create one account for each personality:

| Account setting | G620 | G720 |
| --- | --- | --- |
| Hostname | Address of the emulator host | Address of the emulator host |
| Port | `8620` | `8720` |
| API Base Path | Blank | Blank |
| SSL Configuration | No SSL | No SSL |
| Username/password | The emulator environment values | The emulator environment values |

Use a collector that can reach the emulator host. These direct ports differ from the original development lab's optional reverse-proxy layout and require no URL base path.

## Fault and recovery scenarios

Change a running personality with the scenario helper:

```powershell
# Raise a synthetic unexpected-port-down condition on the G620.
.\tools\Set-BrocadeEmulatorScenario.ps1 -Profile g620 -Scenario port-unexpected-down

# Return it to its healthy baseline.
.\tools\Set-BrocadeEmulatorScenario.ps1 -Profile g620 -Scenario recovery

# Raise and then recover a G720 temperature fault.
.\tools\Set-BrocadeEmulatorScenario.ps1 -Profile g720 -Scenario high-temperature
.\tools\Set-BrocadeEmulatorScenario.ps1 -Profile g720 -Scenario recovery
```

Available scenarios include switch, port, ISL, counter, congestion, hardware, optics, authentication, HTTP, timeout, and malformed/partial-payload conditions. See [docs/SCENARIOS.md](docs/SCENARIOS.md) for the complete list.

Scenarios modify only the selected process's in-memory model. Restarting that emulator or selecting `healthy`/`recovery` restores the baseline.

If custom control ports were selected at startup, pass the same values to the scenario helper:

```powershell
.\tools\Set-BrocadeEmulatorScenario.ps1 -Profile g620 -Scenario crc-errors `
  -G620ControlPort 19620
```

## Authentication and session behaviour

The data API accepts either Basic authentication for read-only GET requests or a Fabric OS-style session flow:

1. `POST /rest/login` with Basic authentication.
2. Use the returned authorization value for data requests.
3. `POST /rest/logout` with the session authorization value.

Environment variables:

| Variable | Purpose | Default when absent |
| --- | --- | --- |
| `BROCADE_EMULATOR_USERNAME` | Data API username | `monitor` |
| `BROCADE_EMULATOR_PASSWORD` | Data API password | `emulator-only` |
| `BROCADE_EMULATOR_CONTROL_TOKEN` | Optional `X-Emulator-Control-Token` value | No token while control API is loopback-only |

The control server refuses a non-loopback bind unless `BROCADE_EMULATOR_CONTROL_TOKEN` is set. The bundled helpers deliberately keep it loopback-only.

## Implemented data routes

| Route | Synthetic data |
| --- | --- |
| `/rest/running/brocade-fibrechannel-switch/fibrechannel-switch` | Local switch identity and state |
| `/rest/running/brocade-fabric/fabric-switch` | Fabric membership |
| `/rest/running/brocade-interface/fibrechannel` | Fibre Channel port identity and state |
| `/rest/running/brocade-interface/fibrechannel-statistics` | Port traffic, error, and congestion counters |
| `/rest/running/brocade-name-server/fibrechannel-name-server` | Connected FC endpoints |
| `/rest/running/brocade-fru/power-supply` | Power supplies |
| `/rest/running/brocade-fru/fan` | Fans |
| `/rest/running/brocade-fru/sensor` | Environmental sensors |
| `/rest/running/brocade-media/media-rdp` | Transceiver diagnostics |
| `/rest/running/brocade-maps/switch-status-policy-report` | MAPS health roll-up |
| `/rest/running/brocade-maps/system-resources` | CPU, memory, flash, and kernel-resource information |

Responses use JSON with Fabric OS-style YANG field names. Unknown routes return an error rather than fabricated generic data.

## TLS support

The Python entry point can serve the data API over TLS when both a PEM certificate and private key are supplied:

```powershell
python -m emulator.brocade_fos_emulator `
  --profile emulator\profiles\ds6620b-g620\profile.json `
  --host 127.0.0.1 --port 8620 --control-port 18620 `
  --tls-cert C:\path\to\certificate.pem `
  --tls-key C:\path\to\private-key.pem
```

Certificate and private-key material must remain outside this repository. The convenience start script intentionally uses HTTP for a contained test lab.

## Tests

Run the unit and API tests from the repository root:

```powershell
python -m unittest discover -s tests -v
```

The suite covers profile integrity, authentication and session handling, supported/unknown routes, scenario transitions, port and counter faults, hardware and optic faults, transport behaviour, and MAPS health.

## Repository layout

```text
.
|-- emulator
|   |-- brocade_fos_emulator.py
|   |-- model.py
|   |-- server.py
|   `-- profiles
|       |-- ds6620b-g620/profile.json
|       `-- ds7720b-g720/profile.json
|-- tools
|   |-- Start-BrocadeEmulators.ps1
|   |-- Stop-BrocadeEmulators.ps1
|   `-- Set-BrocadeEmulatorScenario.ps1
|-- tests/test_emulator.py
|-- docs/SCENARIOS.md
|-- LICENSE
`-- readme.md
```

## Scope and limitations

- This is a selected REST surface, not a switch, SAN simulator, SANnav replacement, or complete Fabric OS implementation.
- It returns JSON and does not emulate the Fabric OS 8.2 XML response model.
- It does not implement SSH, zoning changes, switch configuration, or write operations other than synthetic login/logout and the isolated scenario-control API.
- Timing, session limits, performance, scale, licensing differences, optional fields, TLS behaviour, and role permissions still require testing against the intended physical hardware and firmware.
- G720 switch types and port layouts vary; this profile represents the documented `181.5` interpretation used by the associated management-pack tests.
- The repository contains no real switch captures, production credentials, certificates, session tokens, or private keys.

## License

Released under the [MIT License](LICENSE). See the license file for details.
