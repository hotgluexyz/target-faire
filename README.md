# target-faire

`target-faire` is a Singer target for [Faire](https://www.faire.com), built with the [Hotglue Singer SDK](https://github.com/hotgluexyz/HotglueSingerSDK) for Singer Targets.

It writes order fulfillment/shipment data and product catalog data back to Faire from upstream systems.

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
| `api_key` | Yes | Faire API access token, sent as `X-FAIRE-ACCESS-TOKEN` header |
| `api_url` | No | Base API URL. Defaults to `https://www.faire.com/external-api/v2`. Set to `https://www.faire-stage.com/external-api/v2` for the stage environment |
| `default_taxonomy_type_id` | No | Optional fallback Faire taxonomy type id (`tt_...`) when a Products record has no `category.id` |

Example `config.json`:

```json
{
  "api_key": "your_faire_api_key_here",
  "api_url": "https://www.faire.com/external-api/v2"
}
```

### Source Authentication and Authorization

Faire uses a static API token. Obtain it from the Faire Brand Portal under Settings > API. Pass it as the `api_key` config field; the target sends it as the `X-FAIRE-ACCESS-TOKEN` HTTP header on every request.

## Supported Streams

| Stream | Description |
|---|---|
| `Fulfillments` | Marks a Faire order as shipped and attaches tracking information |
| `Products` | Creates or updates products in the Faire catalog |
| `ProductVariants` | Updates existing product variants (inventory, prices, etc.) via variant PATCH |

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

### Products stream

Accepts records following the unified `Products` shape. A record without a Faire product id creates a new product; a record whose `id` matches the Faire product id format (`p_` plus 10 lowercase alphanumeric characters) updates product metadata.

| Field | Required | Description |
|---|---|---|
| `name` | Yes* | Product name. Required on create; optional on update |
| `sku` | No | Product SKU. Used as the default variant SKU and idempotence token on create when present |
| `idempotence_token` | No | Explicit idempotence key for create. Recommended for retry safety |
| `id` | No | Faire product id for updates when it matches the `p_` + 10 char format. Other upstream ids may be used as create idempotence keys |
| `description` | No | Full product description |
| `short_description` | No | Short description (max 75 chars) |
| `category` | No | `{"id": "tt_...", "name": "..."}`. `id` is the Faire taxonomy type when provided |
| `taxonomy_type` | No | Alias for `category` with Faire-native naming |
| `unit_multiplier` | No | Case size. Defaults to `1` |
| `minimum_order_quantity` | No | Minimum purchase quantity. Defaults to `1` |
| `made_in_country` | No | ISO 3166-1 alpha-3 country code (e.g. `USA`) |
| `currency` | No | Variant price currency. Defaults to `USD` |
| `country` | No | Variant price geo country. Defaults to `USA` |
| `variants` | No** | Array of variant objects (see below) |

\* Required for create only. The Faire API rejects creates without a product name.

\*\* When omitted, a single default variant is built from the product-level `sku`, `price`, and `cost`. At least one variant with wholesale and retail prices is required by the Faire API.


Each variant object:

| Field | Required | Description |
|---|---|---|
| `sku` | No | Variant SKU |
| `price` | Yes* | Retail price in dollars (unified field) |
| `cost` | Yes* | Wholesale price in dollars (unified field) |
| `retail_price_cents` | Yes* | Retail price in cents (alternative to `price`) |
| `wholesale_price_cents` | Yes* | Wholesale price in cents (alternative to `cost`) |
| `available_quantity` | No | Initial inventory quantity |
| `options` | No | `[{"name": "Size", "value": "M"}]` for multi-variant products |

\* The Faire API requires both wholesale and retail prices on each variant. Provide dollar fields (`price`/`cost`) or cent fields (`retail_price_cents`/`wholesale_price_cents`).

Faire API:

- `POST /external-api/v2/products` (create)
- `PATCH /external-api/v2/products/{product_id}` (update metadata)

### ProductVariants stream

Updates a single variant with `PATCH /external-api/v2/products/{product_id}/variants/{variant_id}` ([Faire docs](https://developers.faire.com/docs#/paths/products-product_id--variants--variant_id/patch)).

| Field | Required | Description |
|---|---|---|
| `product_id` | No* | Faire product id (`p_` + 10 chars). May be taken from `id` when it matches that format |
| `variant_id` | No* | Faire variant id (`po_` + 10 chars). May be taken from `id` when it matches that format |
| `id` | No | Convenience: set to the Faire product id or variant id when only one of those is sent |
| `sku` | No** | Used with `GET /products?sku=` to resolve missing product and/or variant ids |
| `currency` | No | Used when deriving `prices` from `cost` / `price`. Defaults to `USD` |
| `country` | No | Geo on derived `prices`. Defaults to `USA` |
| `available_quantity` | No | On-hand quantity |
| `cost` / `price` | No | Wholesale / retail in dollars; used to build `prices` when `prices` and cent fields are absent |
| `wholesale_price_cents` / `retail_price_cents` | No | Sent as-is on the PATCH when set (Faire example includes these fields) |
| `prices` | No | Full Faire price objects; used as-is when present |
| Other PATCH fields | No | Passed through when present: `name`, `sale_state`, `lifecycle_state`, `idempotence_token`, `sku`, `backordered_until`, `tariff_code`, `images`, `options`, `variant_preorder_details`, `measurements`, `gtin`, `orderability_type`, `case_measurements` |

\* At least one way to resolve both Faire ids is required: explicit `product_id` and `variant_id` / `id`, or a `sku` that matches a variant in the catalog (see `sku`).

\*\* Required when the variant id cannot be determined from `id` / `variant_id`, or when `variant_id` is known but `product_id` is missing (lookup finds the product that contains that variant for the same SKU).

## Usage

Pipe tap output directly into the target:

```bash
tap-your-source --config tap_config.json | target-faire --config config.json
```

Or run against a sample Singer file:

```bash
cat sample_payload/fulfillments.singer | target-faire --config .secrets/config.json
cat sample_payload/products.singer | target-faire --config .secrets/config.json
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
