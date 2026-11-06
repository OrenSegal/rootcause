# Deploy the telemetry MCP server to Azure Container Apps

Serves the 25 committed scenario fixtures, read-only, over streamable HTTP. One MCP endpoint
per scenario at `/scenarios/<dir>/mcp`, health at `/healthz`. No credentials, no outbound
calls, no data other than the fixtures in `scenarios/`. `rollback_deploy` is not exposed.

What `main.bicep` creates: a Log Analytics workspace (30-day retention, 0.1 GB/day ingestion
cap), a Container Apps environment, and one container app (0.25 vCPU, 0.5 GiB, 0 to 1
replicas, external HTTPS ingress on port 8000, liveness and readiness probes on `/healthz`).

## Run it locally first

```bash
docker build -t rootcause-telemetry .
docker run --rm -p 8000:8000 rootcause-telemetry
curl -s localhost:8000/healthz
```

Point an MCP client at `http://localhost:8000/scenarios/007-inventory-timeout-cascade/mcp`.
The server is stateless, so a plain JSON-RPC POST works:

```bash
curl -s -X POST localhost:8000/scenarios/007-inventory-timeout-cascade/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"query_logs","arguments":{"level":"error","limit":1}}}'
```

## Deploy by hand

Replace `rootcause-rg` and `eastus` as you like. Run from the repository root.

```bash
az login
az account set --subscription <subscription-id>
az group create --name rootcause-rg --location eastus
```

Push the image first, because the container app cannot start without a pullable image.
With GitHub Container Registry, using a personal access token with `write:packages`:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
docker buildx build --platform linux/amd64 -t ghcr.io/orensegal/rootcause-telemetry:v1 --push .
```

Container Apps pulls without credentials only from a public package. New GHCR packages start
private: open the package's settings on GitHub and set its visibility to Public. The image
contains only the fixtures and open-source code.

```bash
az deployment group create \
  --resource-group rootcause-rg \
  --template-file deploy/azure/main.bicep \
  --parameters imageTag=v1 \
  --query properties.outputs
```

The `url` and `mcpUrlExample` outputs are the public endpoints. Validate the template without
deploying: `az bicep build --file deploy/azure/main.bicep`.

To remove everything: `az group delete --name rootcause-rg`.

## Deploy from GitHub Actions

`.github/workflows/deploy-azure.yml` is manual (`workflow_dispatch`). It builds the image,
pushes it to GHCR, signs in to Azure with OIDC, deploys the Bicep and checks `/healthz`.
No Azure secret is stored: the workflow gets a short-lived token, and the three values below
are identifiers, not credentials.

One-time setup, after the resource group exists. Replace the placeholders.

```bash
SUB=$(az account show --query id -o tsv)
TENANT=$(az account show --query tenantId -o tsv)
APP_ID=$(az ad app create --display-name rootcause-deploy --query appId -o tsv)
az ad sp create --id "$APP_ID"
az role assignment create --assignee "$APP_ID" --role Contributor \
  --scope "/subscriptions/$SUB/resourceGroups/rootcause-rg"
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "rootcause-azure-env",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:OrenSegal/rootcause:environment:azure",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

The role is scoped to the one resource group, not the subscription. Then in the GitHub
repository, create an environment named `azure` and add three secrets to it (or to the
repository):

| Secret | Value |
| --- | --- |
| `AZURE_CLIENT_ID` | `$APP_ID` |
| `AZURE_TENANT_ID` | `$TENANT` |
| `AZURE_SUBSCRIPTION_ID` | `$SUB` |

Run it from the Actions tab or with `gh workflow run deploy-azure.yml`. After the first
push, set the new `rootcause-telemetry` package to Public (see above), or the deploy cannot pull it.

## Cold starts

At zero replicas the first request waits for the replica to start. A local run limited to
0.25 vCPU took about 24 seconds to answer `/healthz`; Azure adds image pull on top. An MCP
client may time out on that first request, so retry once, or hit `/healthz` first.

## Estimated monthly cost

Rates are the East US Consumption plan meters from
[Azure Container Apps pricing](https://azure.microsoft.com/en-us/pricing/details/container-apps/),
read from the Azure Retail Prices API on 2026-11-06, and the rules from
[Billing in Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/billing).

| Meter | Rate |
| --- | --- |
| Active vCPU | $0.000024 per second |
| Active memory | $0.000003 per GiB-second |
| Requests | $0.40 per million |
| Free each month, per subscription | 180,000 vCPU-s, 360,000 GiB-s, 2 million requests |

- **Idle at zero replicas: $0.** The billing page says no resource charges accrue while a revision is scaled to zero.
- **Light use: $0.** At 0.25 vCPU the free vCPU grant covers 720,000 active seconds, about 200 hours a month. The memory grant covers 720,000 seconds at 0.5 GiB. A demo endpoint stays inside both, and 2 million free requests is far above its traffic.
- **Worst case, one replica active all month (30 days): about $19 before the free grants, about $14 after.** That is 0.25 vCPU for 2,592,000 s at $0.000024 plus 0.5 GiB for 2,592,000 s at $0.000003. `maxReplicas` is 1, so it cannot go higher.
- **Log Analytics:** the first 5 GB a month per billing account is free and ingestion above that is $2.30 per GB, per the [Azure Monitor pricing page](https://azure.microsoft.com/en-us/pricing/details/monitor/) and the Retail Prices API. The 0.1 GB/day cap holds ingestion to about 3 GB a month, inside the free allowance.
- **Not used:** private endpoints, a virtual network and planned maintenance carry separate environment charges. This template uses none of them.
- **GHCR** is free for public packages.

Prices change. Check the linked pages before relying on these numbers.
