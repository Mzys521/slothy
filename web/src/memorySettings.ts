/**
 * Personalized-memory settings transport: configuration shape plus load/save calls.
 *
 * 定制记忆设置传输层：配置结构以及读取/保存调用。
 */

import { workRequest } from './workApi'

/** Effective memory-learning configuration edited in settings. / 设置页中可编辑的记忆学习生效配置。 */
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
  strength_c: number
  daily_token_budget: number
}

/** Server response with configuration, defaults and runtime diagnostics. / 服务器响应，含配置、默认值与运行时诊断。 */
export interface MemoryConfigurationPublic {
  revision: number
  configuration: MemoryConfiguration
  defaults: MemoryConfiguration
  runtime: {
    engine: 'rule_review' | 'llm_learning'
    planner_available: boolean
    personalization_enabled: boolean
    model_available: boolean
    effective_model: string | null
    mode: MemoryConfiguration['mode']
    applied_fields: string[]
    reported_tokens: number
    estimated_tokens: number
    budgeted_tokens: number
  }
}

/**
 * Reads the current memory configuration and runtime status.
 *
 * 读取当前记忆配置与运行时状态。
 *
 * @returns Public configuration payload. / 对外的配置载荷。
 */
export function loadMemorySettings() {
  return workRequest<MemoryConfigurationPublic>('/settings/memory')
}

/**
 * Saves a new configuration using optimistic revision checking.
 *
 * 使用乐观版本校验保存新的配置。
 *
 * @param configuration - Full configuration to persist. / 要持久化的完整配置。
 * @param revision - Revision the edit was based on; the server rejects stale writes. / 本次编辑依据的版本号；服务器会拒绝过期写入。
 * @returns Updated public configuration payload. / 更新后的对外配置载荷。
 */
export function saveMemorySettings(configuration: MemoryConfiguration, revision: number) {
  return workRequest<MemoryConfigurationPublic>('/settings/memory', 'PATCH', {
    expected_revision: revision, configuration,
  })
}
