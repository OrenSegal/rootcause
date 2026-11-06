// rootcause telemetry MCP server on Azure Container Apps (Consumption plan).
// One container app, scale to zero, HTTPS ingress, logs into a capped Log Analytics workspace.
// It serves committed fixture telemetry only and holds no secrets.

@description('Region. Defaults to the resource group\'s region.')
param location string = resourceGroup().location

@description('Prefix for resource names.')
param name string = 'rootcause-telemetry'

@description('Image repository. The GHCR package must be public, since no registry credentials are configured.')
param image string = 'ghcr.io/orensegal/rootcause-telemetry'

@description('Image tag to run.')
param imageTag string = 'latest'

@description('Daily Log Analytics ingestion cap in GB, so logs cannot run up a bill.')
param dailyLogQuotaGb string = '0.1'

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${name}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    workspaceCapping: { dailyQuotaGb: json(dailyLogQuotaGb) }
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        // Read at deploy time from the workspace; never stored in the repo.
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false // HTTP redirects to HTTPS
      }
    }
    template: {
      containers: [
        {
          name: 'telemetry'
          image: '${image}:${imageTag}'
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8000 }
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: { path: '/healthz', port: 8000 }
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0
        maxReplicas: 1
      }
    }
  }
}

output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output mcpUrlExample string = 'https://${app.properties.configuration.ingress.fqdn}/scenarios/007-inventory-timeout-cascade/mcp'
