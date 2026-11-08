import { z } from "zod";

export const ErrorEnvelopeSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    details: z.unknown().optional(),
  }),
});

export const HealthSchema = z.object({
  status: z.enum(["ok", "degraded"]),
  database: z.enum(["ok", "unavailable", "not_configured"]),
  schema_ready: z.boolean(),
  missing_tables: z.array(z.string()),
  communication_provider: z.string(),
  message_generator_mode: z.string(),
  checked_at: z.string(),
});

const ByPriority = z.object({ High: z.number(), Medium: z.number(), Low: z.number() });

export const OverviewSchema = z.object({
  generated_at: z.string(),
  leads_total: z.number(),
  leads_analyzed: z.number(),
  leads_not_yet_analyzed: z.number(),
  leads_opted_out: z.number(),
  priority_distribution: ByPriority,
  strategy_distribution: z.record(z.string(), z.number()),
  lost_pipeline_value_total: z.number(),
  lost_pipeline_value_by_priority: ByPriority,
  messages_total: z.number(),
  approvals: z.object({ pending: z.number(), approved: z.number(), rejected: z.number() }),
  sends: z.object({
    sent_simulated: z.number(),
    sent_real: z.number(),
    failed: z.number(),
    in_progress: z.number(),
  }),
});

export const PerformanceSchema = z.object({
  generated_at: z.string(),
  funnel: z.object({
    leads_total: z.number(),
    leads_analyzed: z.number(),
    messages_generated: z.number(),
    messages_pending: z.number(),
    messages_approved: z.number(),
    messages_rejected: z.number(),
    messages_sent_simulated: z.number(),
    messages_sent_real: z.number(),
    send_failures: z.number(),
  }),
  approval_rate: z.number().nullable(),
  send_rate_of_approved: z.number().nullable(),
  pipeline_value_total: z.number(),
  pipeline_value_with_approved_messages: z.number(),
  pipeline_value_contacted_simulated: z.number(),
  pipeline_value_contacted_real: z.number(),
  actual_recovered_revenue: z.number().nullable(),
  recovered_revenue_status: z.string(),
  by_strategy: z.array(
    z.object({
      strategy: z.string(),
      messages: z.number(),
      approved: z.number(),
      rejected: z.number(),
      sent: z.number(),
    }),
  ),
  notes: z.array(z.string()),
});

export const CRMSyncSchema = z.object({
  source: z.string(),
  records_in_source: z.number(),
  created: z.number(),
  updated: z.number(),
  synced_at: z.string(),
  note: z.string(),
});

export type Health = z.infer<typeof HealthSchema>;
export type Overview = z.infer<typeof OverviewSchema>;
export type Performance = z.infer<typeof PerformanceSchema>;
export const AnalysisSchema = z.object({
  id: z.number(),
  lead_id: z.number(),
  created_at: z.string().nullish(),
  loss_reason: z.string(),
  confidence: z.number(),
  evidence: z.array(z.string()),
  email_open_rate: z.number().nullish(),
  engagement_level: z.string().nullish(),
  urgency: z.string().nullish(),
  summary: z.string().nullish(),
  recommended_angle: z.string().nullish(),
  recovery_score: z.number(),
  priority: z.string(),
  score_breakdown: z.record(z.string(), z.unknown()).nullish(),
  score_explanation: z.string().nullish(),
  strategy: z.string(),
  strategy_reasoning: z.string().nullish(),
  recommended_channel: z.string().nullish(),
  strategy_priority: z.string().nullish(),
  next_action: z.string().nullish(),
  strategy_warnings: z.array(z.string()),
});

export const LeadSummarySchema = z.object({
  id: z.number(),
  name: z.string(),
  email: z.string(),
  company: z.string(),
  industry: z.string().nullish(),
  role: z.string().nullish(),
  plan_interested: z.string().nullish(),
  deal_value: z.number(),
  stage_lost_at: z.string().nullish(),
  last_contact_date: z.string().nullish(),
  days_inactive: z.number(),
  opted_out: z.boolean(),
  recovery_score: z.number().nullish(),
  priority: z.string().nullish(),
  strategy: z.string().nullish(),
  last_analyzed_at: z.string().nullish(),
  crm_notes: z.string().nullish(),
});

export const LeadListSchema = z.object({
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
  items: z.array(LeadSummarySchema),
});

export const LeadDetailSchema = LeadSummarySchema.extend({
  crm_records: z.array(
    z.object({
      crm_source: z.string(),
      external_id: z.string(),
      sync_status: z.string(),
      last_synced_at: z.string().nullish(),
    }),
  ),
  latest_analysis: AnalysisSchema.nullable(),
  messages: z.array(
    z.object({
      message_id: z.string(),
      strategy: z.string().nullish(),
      channel: z.string().nullish(),
      approval_status: z.string().nullish(),
      send_status: z.string().nullish(),
      created_at: z.string().nullish(),
    }),
  ),
});

export const MessageSchema = z.object({
  message_id: z.string(),
  customer_id: z.number(),
  customer_name: z.string().nullish(),
  customer_company: z.string().nullish(),
  message_content: z.string(),
  original_message: z.string().nullish(),
  status: z.enum(["pending", "approved", "rejected"]),
  strategy: z.string().nullish(),
  channel: z.string().nullish(),
  created_at: z.string(),
  reviewed_at: z.string().nullish(),
  reviewer: z.string().nullish(),
  edited_at: z.string().nullish(),
  edited_by: z.string().nullish(),
  review_note: z.string().nullish(),
});

export const MessageListItemSchema = MessageSchema.extend({ send_status: z.string().nullish() });
export const MessageListSchema = z.array(MessageListItemSchema);

export const GenerateMessageSchema = z.object({
  message: MessageSchema,
  generation_status: z.string(),
  analysis_id: z.number(),
  recovery_score: z.number(),
  priority: z.string(),
  strategy: z.string(),
});

export const SendResponseSchema = z.object({
  communication: z.object({
    message_id: z.string(),
    status: z.string(),
    sent_at: z.string().nullish(),
    provider: z.string().nullish(),
    recipient: z.string().nullish(),
  }),
  delivery_mode: z.enum(["simulated", "real"]),
  crm_update: z.record(z.string(), z.unknown()),
});

export type LeadSummary = z.infer<typeof LeadSummarySchema>;
export type MessageItem = z.infer<typeof MessageListItemSchema>;