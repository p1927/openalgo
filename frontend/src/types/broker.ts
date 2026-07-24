export type BrokerAuthFlow =
  | 'callback'
  | 'oauth_external'
  | 'oauth_init'
  | 'totp'
  | 'api_key_env'

export interface BrokerDescriptor {
  id: string
  display_name: string
  description: string
  broker_type: string
  auth_flow: BrokerAuthFlow | string
  supported_exchanges: string[]
  is_default: boolean
  credentials_configured: boolean
  connect_url: string | null
  login_notice: string | null
  requires_app_restart: boolean
}

export interface BrokersListResponse {
  status: string
  default_broker: string
  brokers: BrokerDescriptor[]
}

export interface PrepareConnectResponse {
  status: string
  broker: string
  connect_url: string
  auth_flow: string
  login_notice: string | null
  redirect_url: string
  message?: string
}
