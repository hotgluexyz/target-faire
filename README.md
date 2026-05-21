# target-faire

`target-faire` is a Singer target for [Faire](https://www.faire.com), built with the [Hotglue Singer SDK](https://github.com/hotgluexyz/HotglueSingerSDK) for Singer Targets.

It writes order fulfillment/shipment data back to Faire as orders are shipped in an upstream system (e.g. Linnworks).

## Installation

```bash
pip install target-faire
```

Or directly from the repo:

```bash
pip install git+https://github.com/hotgluexyz/target-faire.git
```

## Configuration

### Accepted Config Options

| Field | Required | Description |
|---|---|---|
| `api_token` | Yes | Faire API access token, sent as `X-FAIRE-ACCESS-TOKEN` header |
| `api_url` | No | Base API URL. Defaults to `https://www.faire.com/external-api/v2`. Set to `https://www.faire-stage.com/external-api/v2` for the stage environment |

Example `config.json`:

```json
{
  "api_token": "your_faire_api_token_here",
  "api_url": "https://www.faire.com/external-api/v2"
}
```

### Source Authentication and Authorization

Faire uses a static API token. Obtain it from the Faire Brand Portal under Settings > API. Pass it as the `api_token` config field; the target sends it as the `X-FAIRE-ACCESS-TOKEN` HTTP header on every request.

## Supported Streams

| Stream | Description |
|---|---|
| `Fulfillments` | Marks a Faire order as shipped and attaches tracking information |

### Fulfillments stream

Accepts records following the unified `SalesOrder` shape. Required and optional fields:

| Field | Required | Description |
|---|---|---|
| `order_id` | Yes | Faire order ID (e.g. `bo_xxxx`) |
| `tracking_number` | Yes | Carrier tracking code |
| `carrier` | Yes | Carrier name (e.g. `UPS`, `FEDEX`, `USPS`) |
| `shipping_cost_cents` | No | Shipping cost in cents (integer). Defaults to 0 if omitted |
| `total_shipping` | No | Shipping cost in dollars (float). Used if `shipping_cost_cents` is absent |
| `currency` | No | ISO currency code, defaults to `USD` |
| `item_ids` | No | List of specific Faire order item IDs to include in the shipment |

Faire API: `POST /external-api/v2/orders/{order_id}/shipments`

## Usage

Pipe tap output directly into the target:

```bash
tap-linnworks --config tap_config.json | target-faire --config config.json
```

Or run against a sample Singer file:

```bash
cat sample_payload/data.singer | target-faire --config .secrets/config.json
```

## Developer Resources

Set up a virtual environment and install dependencies:

```bash
python -m venv .venv
.venv/bin/pip install -e . ruff
```

Run lint:

```bash
.venv/bin/ruff check .
```

Verify the CLI:

```bash
.venv/bin/target-faire --version
.venv/bin/target-faire --about
```

Run tests:

```bash
.venv/bin/pytest target_faire/tests/
```
