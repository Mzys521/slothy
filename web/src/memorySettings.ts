import { workRequest } from './workApi'

export interface MemoryConfiguration {
  mode: 'off' | 'shadow' | 'auto' | 'review'
  model: string | null
  batch_size: number
  idle_seconds: number
  consolidate_every: number
  forget_interval_hours: number
  active_limit: number
  auto_confirm_score: number
  observed_min_threads: number
  episodic_ttl_days: number
  fact_ttl_days: number
  retention_days: number
  daily_token_budget: number
}

export interface MemoryConfigurationPublic {
  revision: number
  configuration: MemoryConfiguration
  defaults: MemoryConfiguration
  runtime: {
    engine: 'rule_review'
    planner_available: false
    personalization_enabled: boolean
  }
}

export function loadMemorySettings() {
  return workRequest<MemoryConfigurationPublic>('/settings/memory')
}

export function saveMemorySettings(configuration: MemoryConfiguration, revision: number) {
  return workRequest<MemoryConfigurationPublic>('/settings/memory', 'PATCH', {
    expected_revision: revision, configuration,
  })
}
