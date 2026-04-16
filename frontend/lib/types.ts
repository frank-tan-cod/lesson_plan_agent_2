export type DocType = "lesson" | "presentation";

export interface AuthToken {
  access_token: string;
  token_type: "bearer";
}

export interface User {
  id: string;
  username: string;
}

export interface Plan {
  id: string;
  title: string;
  doc_type: DocType;
  subject?: string | null;
  grade?: string | null;
  content: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface PlanListResponse {
  items: Plan[];
  total: number;
}

export type MiniGameTemplate = "single_choice" | "true_false" | "flip_cards";

export interface MiniGame {
  id: string;
  template: MiniGameTemplate;
  title: string;
  description?: string;
  source_section?: string | null;
  learning_goal?: string | null;
  html_url?: string | null;
  data: Record<string, unknown>;
}

export interface Conversation {
  id: string;
  plan_id: string;
  started_at: string;
  ended_at?: string | null;
  status: string;
  summary?: string | null;
  metadata: Record<string, unknown>;
}

export interface Savepoint {
  id: string;
  plan_id: string;
  conversation_id?: string | null;
  label: string;
  snapshot?: Record<string, unknown> | null;
  created_at: string;
}

export interface RestoreResponse {
  status: string;
  plan_id: string;
  savepoint_id: string;
}

export interface KnowledgeFile {
  id: string;
  user_id: string;
  filename: string;
  file_type: string;
  storage_path: string;
  description?: string | null;
  metadata: KnowledgeFileMetadata;
  created_at: string;
}

export interface KnowledgeFileListResponse {
  items: KnowledgeFile[];
  total: number;
}

export interface KnowledgeFileMetadata {
  source?: string | null;
  trigger?: string | null;
  doc_type?: DocType | string | null;
  indexed?: boolean | null;
  extension?: string | null;
  chunk_count?: number | null;
  size_bytes?: number | null;
  generated_from?: string | null;
  source_plan_id?: string | null;
  plan_id?: string | null;
  plan_title?: string | null;
  savepoint_id?: string | null;
  savepoint_label?: string | null;
  tags?: string[] | null;
  [key: string]: unknown;
}

export interface KnowledgeSearchResult {
  file_id: string;
  filename: string;
  file_type: string;
  text_snippet: string;
  relevance_score: number;
  matched_snippets?: string[];
  summary?: string | null;
  match_reason?: string | null;
  source?: string | null;
  trigger?: string | null;
  doc_type?: string | null;
  search_strategy?: string | null;
}

export interface KnowledgeAnswerCitation {
  file_id: string;
  filename: string;
  file_type: string;
  text_snippet: string;
  summary?: string | null;
  match_reason?: string | null;
  source?: string | null;
  trigger?: string | null;
  doc_type?: string | null;
  relevance_score: number;
}

export interface KnowledgeAnswerResponse {
  answer: string;
  citations: KnowledgeAnswerCitation[];
  results: KnowledgeSearchResult[];
  used_llm: boolean;
}

export interface GeneratePresentationResponse {
  presentation_id: string;
}

export type PresentationTheme = "scholastic_blue" | "forest_green" | "sunrise_orange";
export type PresentationDensity = "comfortable" | "balanced" | "compact";
export type TeachingPacePreference = "compact" | "balanced" | "thorough";
export type InteractionLevelPreference = "lecture" | "balanced" | "interactive";
export type DetailLevelPreference = "summary" | "balanced" | "step_by_step";
export type LanguageStylePreference = "rigorous" | "conversational" | "encouraging";
export type VisualFocusPreference = "auto" | "text_first" | "visual_first";

export interface PresentationStylePayload {
  theme: PresentationTheme;
  density: PresentationDensity;
  school_name?: string | null;
  logo_url?: string | null;
  logo_file_id?: string | null;
}

export interface TempPreferencesPayload {
  teaching_pace?: TeachingPacePreference;
  interaction_level?: InteractionLevelPreference;
  detail_level?: DetailLevelPreference;
  language_style?: LanguageStylePreference;
  visual_focus?: VisualFocusPreference;
  other_notes?: string;
}

export interface PreferencePreset {
  id: string;
  user_id: string;
  name: string;
  description?: string | null;
  prompt_injection: string;
  structured_preferences: TempPreferencesPayload;
  tags: string[];
  is_active: boolean;
  created_at: string;
}

export interface PreferenceSuggestion {
  name: string;
  description: string;
  prompt_injection: string;
  structured_preferences: TempPreferencesPayload;
  tags: string[];
}

export type EditorEventName =
  | "conversation"
  | "status"
  | "tool"
  | "tool_result"
  | "follow_up"
  | "confirmation_required"
  | "delta"
  | "done"
  | "error";

export interface EditorConversationEvent {
  conversation_id: string;
}

export interface EditorToolEvent {
  conversation_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
}

export interface EditorToolResultEvent {
  conversation_id: string;
  tool_name: string;
  ok: boolean;
  summary?: string;
  result: Record<string, unknown>;
}

export interface EditorStatusEvent {
  content: string;
}

export interface EditorPendingTask {
  type: "modify" | "query" | "follow_up" | "reply" | "confirm" | "cancel";
  tool_name?: string | null;
  target?: string | null;
  action?: string | null;
  proposed_content?: string | null;
  response?: string | null;
  parameters: Record<string, unknown>;
}

export interface EditorFollowUpEvent {
  conversation_id: string;
  type: "follow_up";
  question: string;
  options?: string[] | null;
  previous_user_message?: string | null;
  completed_steps?: string[] | null;
  remaining_tasks?: EditorPendingTask[] | null;
}

export interface EditorConfirmationEvent {
  conversation_id: string;
  type: "confirmation_required";
  operation_description: string;
  proposed_changes: string;
  tool_to_confirm: string;
  tool_args: Record<string, unknown>;
}

export interface EditorDeltaEvent {
  content: string;
}

export interface EditorDoneEvent {
  conversation_id: string;
  reply: string;
  plan?: Plan | null;
}

export interface EditorErrorEvent {
  message: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  kind?: "text" | "tool" | "tool_result" | "follow_up" | "confirmation" | "error" | "status";
  createdAt: number;
}
