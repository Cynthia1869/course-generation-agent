<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from "vue";
import { marked } from "marked";
import {
  ApiError,
  createThread, fetchDiff, fetchThread,
  retractLastMessage, sendMessage, streamThread, submitReview,
} from "./lib/api";
import type { ReviewBatch, ThreadState } from "./types";

// ── state ──────────────────────────────────────────────────────────────────
const threadId    = ref("");
const threadState = ref<ThreadState | null>(null);
const content     = ref("");
const activeTab   = ref<"preview" | "diff">("preview");
const diffText    = ref("");
const sending     = ref(false);
const booting     = ref(false);
const bootError   = ref("");
const reviewDraft = ref<Record<string, { action: "approve" | "edit" | "reject"; edited: string }>>({});
const eventSrc    = ref<EventSource | null>(null);
const scrollEl    = ref<HTMLDivElement | null>(null);
const inputEl     = ref<HTMLTextAreaElement | null>(null);
const streamingAssistant = ref("");
const streamingAssistantActive = ref(false);

// ── streaming / processing state ───────────────────────────────────────────
const streamingMarkdown = ref("");   // accumulates token_stream chunks in real-time
const processing        = ref(false); // true while backend is working (after send, before response)

const displayMarkdownHtml = computed(() => {
  const md = streamingMarkdown.value || threadState.value?.draft_artifact?.markdown || "";
  return marked.parse(md) as string;
});
const isStreaming = computed(() => !!streamingMarkdown.value);

// ── panel resize ───────────────────────────────────────────────────────────
const chatW    = ref(440);
const resizing = ref(false);
function startResize(e: MouseEvent) {
  e.preventDefault();
  resizing.value = true;
  const move = (ev: MouseEvent) => {
    chatW.value = Math.max(300, Math.min(720, window.innerWidth - ev.clientX));
  };
  const up = () => {
    resizing.value = false;
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
  };
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
}

const messages     = computed(() => threadState.value?.messages ?? []);
const visibleMessages = computed(() => {
  if (!streamingAssistantActive.value) return messages.value;
  const cloned = [...messages.value];
  const last = cloned[cloned.length - 1];
  if (last?.role === "assistant") {
    return cloned.slice(0, -1);
  }
  return cloned;
});
const latestReview = computed<ReviewBatch | null>(() => {
  const b = threadState.value?.review_batches ?? [];
  return b.length ? b[b.length - 1] : null;
});
const markdownHtml = computed(() =>
  marked.parse(threadState.value?.draft_artifact?.markdown ?? ""),
);
function renderAssistantMessage(content: string) {
  const normalized = content
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]+\n/g, "\n")
    .trim();
  return marked.parse(normalized) as string;
}
function renderUserMessage(content: string) {
  return content.replace(/\n{3,}/g, "\n\n").trim();
}
const hasContent = computed(() =>
  !!(threadState.value?.draft_artifact?.markdown || streamingMarkdown.value),
);
const isEmpty    = computed(() => messages.value.length === 0 && !booting.value);
// Whether the last message is user's and the thread is still collecting (retract is safe)
const canRetract = computed(() => {
  const msgs = messages.value;
  return (
    !processing.value &&
    msgs.length > 0 &&
    msgs[msgs.length - 1].role === "user" &&
    threadState.value?.status === "collecting_requirements"
  );
});

// ── bootstrap ─────────────────────────────────────────────────────────────
async function bootstrap() {
  booting.value = true;
  bootError.value = "";
  try {
    const { thread } = await createThread();
    await openThread(thread.thread_id);
  } catch (e) {
    bootError.value = "连接失败，请刷新页面重试。";
    console.error(e);
  } finally {
    booting.value = false;
  }
}

async function openThread(newThreadId: string) {
  eventSrc.value?.close();
  threadId.value = newThreadId;
  await refreshThread();
  eventSrc.value = streamThread(threadId.value, async (e, type) => {
    if (type === "assistant_token") {
      const data = JSON.parse(e.data) as { content: string };
      streamingAssistantActive.value = true;
      streamingAssistant.value += data.content;
      scrollBottom();
    } else if (type === "assistant_stream_end") {
      processing.value = false;
      await refreshThread();
      streamingAssistant.value = "";
      streamingAssistantActive.value = false;
      scrollBottom();
    } else if (type === "token_stream") {
      const data = JSON.parse(e.data) as { content: string };
      streamingMarkdown.value += data.content;
    } else if (type === "assistant_message") {
      processing.value = false;
      streamingMarkdown.value = "";
      await refreshThread();
      scrollBottom();
    } else if (type === "artifact_updated") {
      processing.value = false;
      streamingMarkdown.value = "";
      await refreshThread();
      scrollBottom();
    } else if (type === "message_retracted") {
      processing.value = false;
      streamingMarkdown.value = "";
      await refreshThread();
    } else if (["review_batch", "node_update"].includes(type)) {
      await refreshThread();
      scrollBottom();
    }
  });
}

async function refreshThread() {
  const res = await fetchThread(threadId.value);
  threadState.value = res.state;
  const art = threadState.value?.draft_artifact;
  if (art && art.version > 1) {
    const d = await fetchDiff(threadId.value, art.version, art.version - 1);
    diffText.value = d.diff;
  }
}

function isThreadNotFound(error: unknown) {
  return error instanceof ApiError && error.status === 404;
}

async function recreateThreadAndReconnect() {
  const { thread } = await createThread();
  await openThread(thread.thread_id);
}

onMounted(bootstrap);
onUnmounted(() => eventSrc.value?.close());

// ── send ───────────────────────────────────────────────────────────────────
async function handleSend() {
  const text = content.value.trim();
  if (!text || sending.value || processing.value || !threadId.value) return;
  sending.value = true;
  content.value = "";
  resetHeight();
  try {
    await sendMessage(threadId.value, text);
    processing.value = true; // backend is now working
    scrollBottom();
  } catch (error) {
    if (isThreadNotFound(error)) {
      await recreateThreadAndReconnect();
      await sendMessage(threadId.value, text);
      processing.value = true;
      scrollBottom();
      return;
    }
    content.value = text;
  } finally {
    sending.value = false;
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); }
}

function resetHeight() {
  if (inputEl.value) { inputEl.value.style.height = "24px"; }
}

function autoResize() {
  const el = inputEl.value;
  if (!el) return;
  el.style.height = "24px";
  el.style.height = Math.min(el.scrollHeight, 180) + "px";
}

function scrollBottom() {
  nextTick(() => {
    if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
  });
}

// ── retract ────────────────────────────────────────────────────────────────
async function retractMessage() {
  if (!canRetract.value || !threadId.value) return;
  try {
    await retractLastMessage(threadId.value);
  } catch (error) {
    if (isThreadNotFound(error)) {
      await recreateThreadAndReconnect();
      return;
    }
    console.error(error);
  }
}

// ── new conversation ────────────────────────────────────────────────────────
async function newConversation() {
  eventSrc.value?.close();
  eventSrc.value = null;
  threadId.value = "";
  threadState.value = null;
  streamingMarkdown.value = "";
  processing.value = false;
  bootError.value = "";
  content.value = "";
  diffText.value = "";
  activeTab.value = "preview";
  reviewDraft.value = {};
  await bootstrap();
}

// ── review ─────────────────────────────────────────────────────────────────
function setAction(id: string, action: "approve" | "edit" | "reject") {
  reviewDraft.value[id] = { ...(reviewDraft.value[id] ?? { action, edited: "" }), action };
}
async function handleReviewSubmit() {
  if (!latestReview.value) return;
  await submitReview(
    threadId.value,
    latestReview.value.review_batch_id,
    latestReview.value.suggestions.map((s) => {
      const d = reviewDraft.value[s.suggestion_id] ?? { action: "approve", edited: "" };
      return { suggestion_id: s.suggestion_id, action: d.action,
        edited_suggestion: d.edited || undefined, reviewer_id: "default-user", comment: "" };
    }),
  );
}

// hint chips
const chips = [
  "我想制作一门 Python 入门课，面向零基础学员",
  "帮我设计一个 UX 设计在线课程大纲",
  "制作面向中学生的数学思维训练课",
];
function fillChip(text: string) {
  content.value = text;
  nextTick(() => { inputEl.value?.focus(); autoResize(); });
}
</script>

<template>
  <div class="shell" :class="{ resizing }">

    <!-- ══ LEFT: Artifact panel ══════════════════════════════ -->
    <aside class="artifact-panel">
      <div class="artifact-header">
        <div class="artifact-title">
          <svg class="title-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
            <polyline points="14 2 14 8 20 8"/>
          </svg>
          <span>课程草稿</span>
          <span v-if="threadState?.draft_artifact" class="artifact-version">
            v{{ threadState.draft_artifact.version }}
          </span>
          <!-- streaming badge -->
          <span v-if="isStreaming" class="artifact-generating">
            <span class="gen-dot" /><span class="gen-dot" /><span class="gen-dot" />
            生成中
          </span>
        </div>
        <div class="tab-row" role="tablist">
          <button role="tab" :aria-selected="activeTab==='preview'" :class="['tab',{on:activeTab==='preview'}]" @click="activeTab='preview'">预览</button>
          <button role="tab" :aria-selected="activeTab==='diff'"    :class="['tab',{on:activeTab==='diff'}]"    @click="activeTab='diff'">对比</button>
        </div>
      </div>

      <div class="artifact-body">
        <Transition name="t-fade" mode="out-in">
          <div v-if="activeTab==='preview'" key="preview">
            <div v-if="!hasContent" class="artifact-empty">
              <div class="artifact-empty-icon">
                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M14 2H6a2 2 0 0 0-2 2v16c0 1.1.9 2 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
              </div>
              <p class="artifact-empty-h">课程内容将在这里显示</p>
              <p class="artifact-empty-p">在右侧与 AI 对话后，生成的课程内容会实时呈现在此。</p>
            </div>
            <div v-else class="prose" v-html="displayMarkdownHtml" />
            <div v-if="isStreaming" class="stream-cursor" aria-hidden="true" />
          </div>
          <pre v-else key="diff" class="diff-view">{{ diffText || "暂无版本差异。" }}</pre>
        </Transition>
      </div>

      <!-- Review panel -->
      <Transition name="t-slide">
        <div v-if="latestReview" class="review-pane">
          <div class="review-score-row">
            <span class="review-score-label">综合评分</span>
            <div class="review-track"><div class="review-fill" :style="{width:latestReview.total_score+'%'}" /></div>
            <span class="review-score-num">{{ latestReview.total_score }}</span>
          </div>
          <div class="sug-list">
            <div v-for="s in latestReview.suggestions" :key="s.suggestion_id" class="sug-card">
              <div class="sug-row">
                <span :class="['sev',s.severity]">{{ s.severity }}</span>
                <span class="sug-prob">{{ s.problem }}</span>
              </div>
              <p class="sug-body">{{ s.suggestion }}</p>
              <div class="sug-btns">
                <button :class="['sug-btn',{on:reviewDraft[s.suggestion_id]?.action==='approve'}]" @click="setAction(s.suggestion_id,'approve')">通过</button>
                <button :class="['sug-btn',{on:reviewDraft[s.suggestion_id]?.action==='edit'}]"    @click="setAction(s.suggestion_id,'edit')">修改</button>
                <button :class="['sug-btn red',{on:reviewDraft[s.suggestion_id]?.action==='reject'}]" @click="setAction(s.suggestion_id,'reject')">驳回</button>
              </div>
              <textarea v-if="reviewDraft[s.suggestion_id]?.action==='edit'" v-model="reviewDraft[s.suggestion_id].edited" class="sug-edit" rows="2" placeholder="输入修改意见…" />
            </div>
          </div>
          <button class="review-submit-btn" @click="handleReviewSubmit">提交审核结果</button>
        </div>
      </Transition>
    </aside>

    <!-- ══ Resize handle ════════════════════════════════════ -->
    <div class="resize-handle" :class="{ active: resizing }" @mousedown="startResize" />

    <!-- ══ RIGHT: Chat panel ════════════════════════════════ -->
    <main class="chat-panel" :style="{ width: chatW + 'px' }">

      <!-- Top bar -->
      <div class="chat-topbar">
        <button class="new-chat-btn" :disabled="booting" @click="newConversation" title="新对话">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true">
            <path d="M12 5v14M5 12h14"/>
          </svg>
          新对话
        </button>
        <div class="model-chip">
          <div class="claude-logo" aria-hidden="true">
            <img src="/icon.png" width="22" height="22" alt="" style="display:block;border-radius:4px;" />
          </div>
          <span class="model-name">制课 Agent</span>
          <span :class="['model-status', bootError ? 'err' : booting || processing ? 'loading' : 'ok']">
            {{ bootError ? '连接失败' : booting ? '连接中' : processing ? '思考中' : '就绪' }}
          </span>
        </div>
        <div class="topbar-spacer" aria-hidden="true" />
      </div>

      <!-- Messages scroll area -->
      <div ref="scrollEl" class="messages-scroll" role="log" aria-label="对话" aria-live="polite">
        <div class="messages-inner">

          <!-- Welcome screen -->
          <Transition name="t-welcome">
            <div v-if="isEmpty" class="welcome">
              <div class="welcome-mark" aria-hidden="true">
                <img src="/icon.png" width="56" height="56" alt="" style="display:block;border-radius:12px;" />
              </div>
              <h2 class="welcome-h">你好，我是制课 Agent</h2>
              <p class="welcome-p">告诉我你想创建什么课程，我会引导你逐步完成。</p>
              <div class="chips">
                <button v-for="c in chips" :key="c" class="chip" @click="fillChip(c)">{{ c }}</button>
              </div>
            </div>
          </Transition>

          <!-- Boot error -->
          <div v-if="bootError" class="error-banner">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            {{ bootError }}
          </div>

          <!-- Boot spinner -->
          <div v-if="booting && !bootError" class="boot-spinner">
            <span /><span /><span />
          </div>

          <!-- Message list with entrance animations -->
          <TransitionGroup name="t-msg" tag="div" class="msg-list">
            <div v-for="msg in visibleMessages" :key="msg.message_id" :class="['msg', msg.role]">

              <!-- USER -->
              <div v-if="msg.role==='user'" class="user-row">
                <button
                  v-if="canRetract && msg === messages[messages.length - 1]"
                  class="retract-btn"
                  title="撤回"
                  @click="retractMessage"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2">
                    <path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>
                  </svg>
                  撤回
                </button>
                <div class="user-bubble">{{ renderUserMessage(msg.content) }}</div>
              </div>

              <!-- ASSISTANT -->
              <div v-else-if="msg.role==='assistant'" class="ai-row">
                <div class="ai-avatar" aria-hidden="true">
                  <img src="/icon.png" width="16" height="16" alt="" style="display:block;border-radius:3px;" />
                </div>
                <div class="ai-body">
                  <span class="ai-name">制课 Agent</span>
                  <div class="ai-text" v-html="renderAssistantMessage(msg.content)" />
                </div>
              </div>

            </div>
          </TransitionGroup>

          <Transition name="t-fade">
            <div v-if="streamingAssistantActive" class="msg assistant">
              <div class="ai-row">
                <div class="ai-avatar" aria-hidden="true">
                  <img src="/icon.png" width="16" height="16" alt="" style="display:block;border-radius:3px;" />
                </div>
                <div class="ai-body">
                  <span class="ai-name">制课 Agent</span>
                  <div class="ai-text" v-html="renderAssistantMessage(streamingAssistant)" />
                </div>
              </div>
            </div>
          </Transition>

          <!-- Typing / thinking indicator -->
          <Transition name="t-fade">
            <div v-if="(sending || processing) && !streamingAssistantActive" class="msg assistant">
              <div class="ai-row">
                <div class="ai-avatar" aria-hidden="true">
                  <img src="/icon.png" width="16" height="16" alt="" style="display:block;border-radius:3px;" />
                </div>
                <div class="ai-body">
                  <span class="ai-name">制课 Agent</span>
                  <div v-if="processing && !sending" class="thinking-indicator">
                    <div class="thinking-orb" />
                    <span class="thinking-label">正在思考…</span>
                  </div>
                  <div v-else class="typing"><span /><span /><span /></div>
                </div>
              </div>
            </div>
          </Transition>

        </div>
      </div>

      <!-- Composer — Claude-style centered input -->
      <div class="composer-area">
        <div :class="['composer-box', { focused: false }]">
          <textarea
            ref="inputEl"
            v-model="content"
            class="composer-ta"
            placeholder="给制课 Agent 发送消息…"
            rows="1"
            :disabled="!!bootError || booting || processing"
            @keydown="handleKeydown"
            @input="autoResize"
            aria-label="输入消息"
          />
          <button
            :class="['send-btn', { active: !!content.trim() && !sending }]"
            :disabled="!content.trim() || sending || booting || !!bootError || processing"
            :aria-label="sending ? '发送中' : '发送'"
            @click="handleSend"
          >
            <Transition name="t-icon" mode="out-in">
              <svg v-if="!sending" key="arrow" width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M2.01 21 23 12 2.01 3 2 10l15 2-15 2z"/>
              </svg>
              <div v-else key="spin" class="spin" aria-hidden="true" />
            </Transition>
          </button>
        </div>
        <p class="composer-hint">Enter 发送 · Shift+Enter 换行</p>
      </div>

    </main>
  </div>
</template>

<style scoped>
/* ═══════════════════════════════════════════
   GLOBAL RESET
═══════════════════════════════════════════ */
:global(*) { box-sizing: border-box; margin: 0; padding: 0; }
:global(html, body) {
  height: 100%;
  overflow: hidden;
  /* Anthropic Sans → Inter / PingFang SC */
  font-family: "Inter", "PingFang SC", "Noto Sans SC", ui-sans-serif, system-ui, sans-serif;
  font-feature-settings: "cv02","cv03","cv04","cv11";
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  background: #f5f4ed; /* Parchment */
  color: #141413;      /* Near Black */
}

/* ═══════════════════════════════════════════
   LAYOUT
═══════════════════════════════════════════ */
.shell {
  display: flex;
  height: 100dvh;
  overflow: hidden;
  background: #f5f4ed; /* Parchment */
}
.shell.resizing { cursor: ew-resize; user-select: none; }

/* ═══════════════════════════════════════════
   LEFT — Artifact panel
═══════════════════════════════════════════ */
.artifact-panel {
  display: flex;
  flex-direction: column;
  flex: 1 1 0;
  min-width: 240px;
  background: #faf9f5; /* Ivory */
  overflow: hidden;
}

.artifact-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  height: 52px;
  border-bottom: 1px solid #f0eee6; /* Border Cream */
  background: #faf9f5;
  flex-shrink: 0;
  gap: 12px;
}

/* ── topbar layout ── */
.chat-topbar {
  justify-content: space-between !important;
}

.new-chat-btn {
  display: flex; align-items: center; gap: 5px;
  background: none; border: 1px solid #e8e6dc; /* Border Warm */
  border-radius: 8px; padding: 5px 11px;
  font-size: 13px; font-weight: 500;
  color: #4d4c48; /* Charcoal Warm */
  font-family: inherit; cursor: pointer;
  /* Ring shadow */
  box-shadow: #faf9f5 0px 0px 0px 0px, #d1cfc5 0px 0px 0px 1px;
  transition: box-shadow .12s, color .12s;
}
.new-chat-btn:hover {
  color: #141413;
  box-shadow: #faf9f5 0px 0px 0px 0px, #c96442 0px 0px 0px 1px;
}
.new-chat-btn:disabled { opacity: .45; cursor: default; }

.topbar-spacer { width: 80px; flex-shrink: 0; } /* mirrors new-chat-btn width for centering */

/* ── artifact title ── */
.artifact-title {
  display: flex; align-items: center; gap: 6px;
  font-size: 13px; font-weight: 500;
  color: #141413; /* Near Black */
  white-space: nowrap; letter-spacing: -0.01em;
}

.title-icon { color: #87867f; flex-shrink: 0; } /* Stone Gray */

.artifact-version {
  font-size: 11px; font-weight: 500;
  color: #5e5d59; /* Olive Gray */
  background: #e8e6dc; /* Warm Sand */
  border-radius: 6px; padding: 1px 7px;
  letter-spacing: 0.01em;
}

/* ── streaming badge ── */
.artifact-generating {
  display: inline-flex; align-items: center; gap: 4px;
  font-size: 11px; font-weight: 500;
  color: #c96442; /* Terracotta */
  letter-spacing: 0.01em;
}
.gen-dot {
  width: 5px; height: 5px; border-radius: 50%;
  background: #c96442;
  display: inline-block;
  animation: bounce-dot 1.2s ease-in-out infinite;
}
.gen-dot:nth-child(2) { animation-delay: .18s; }
.gen-dot:nth-child(3) { animation-delay: .36s; }

/* blinking cursor at end of streamed content */
.stream-cursor {
  display: inline-block; width: 2px; height: 1.1em;
  background: #c96442; margin-left: 2px; vertical-align: text-bottom;
  animation: blink-cursor .85s step-end infinite;
}
@keyframes blink-cursor {
  0%,100% { opacity: 1; }
  50%      { opacity: 0; }
}

/* ── Tabs ── */
.tab-row {
  display: flex;
  background: #e8e6dc; /* Warm Sand */
  border-radius: 8px; padding: 3px; gap: 2px; flex-shrink: 0;
}

.tab {
  border: none; background: none; border-radius: 6px;
  padding: 4px 14px; font-size: 12.5px; font-weight: 500;
  color: #5e5d59; /* Olive Gray */
  cursor: pointer; font-family: inherit;
  transition: background 0.12s, color 0.12s, box-shadow 0.12s;
  white-space: nowrap;
}

.tab.on {
  background: #faf9f5; /* Ivory */
  color: #141413; /* Near Black */
  /* Ring shadow — Level 2 */
  box-shadow: #faf9f5 0px 0px 0px 0px, #d1cfc5 0px 0px 0px 1px;
}

.tab:focus-visible { outline: 2px solid #3898ec; outline-offset: 1px; } /* Focus Blue */

/* ── Artifact body ── */
.artifact-body {
  flex: 1; overflow-y: auto;
  padding: 36px 40px;
  scrollbar-width: thin;
  scrollbar-color: #d1cfc5 transparent; /* Ring Warm */
}

/* ── Empty state ── */
.artifact-empty {
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  text-align: center; gap: 12px; min-height: 320px;
}

.artifact-empty-icon { color: #d1cfc5; } /* Ring Warm */
.artifact-empty-h {
  font-size: 15px; font-weight: 500;
  color: #4d4c48; /* Charcoal Warm */
  letter-spacing: -0.01em;
}
.artifact-empty-p {
  font-size: 13.5px; color: #87867f; /* Stone Gray */
  max-width: 240px; line-height: 1.60;
}

/* ── Prose / Markdown ── */
.prose {
  font-size: 15px; line-height: 1.60; /* Relaxed — Claude standard */
  color: #141413; max-width: 680px;
}
/* Anthropic Serif → Georgia for headings */
.prose :deep(h1) {
  font-family: Georgia, "Noto Serif SC", serif;
  font-size: 32px; font-weight: 500; /* Single-weight serif rule */
  line-height: 1.10; letter-spacing: -0.01em;
  margin-bottom: 16px; color: #141413;
}
.prose :deep(h2) {
  font-family: Georgia, "Noto Serif SC", serif;
  font-size: 20.8px; font-weight: 500; line-height: 1.20;
  margin: 28px 0 10px; padding-bottom: 8px;
  border-bottom: 1px solid #f0eee6; /* Border Cream */
  color: #141413;
}
.prose :deep(h3) {
  font-family: Georgia, "Noto Serif SC", serif;
  font-size: 17px; font-weight: 500; line-height: 1.30;
  margin: 20px 0 8px; color: #141413;
}
.prose :deep(p)  { margin-bottom: 14px; color: #4d4c48; } /* Charcoal Warm */
.prose :deep(ul), .prose :deep(ol) { padding-left: 20px; margin-bottom: 14px; }
.prose :deep(li) { margin-bottom: 5px; color: #4d4c48; }
.prose :deep(code) {
  background: #f5f4ed; /* Parchment */
  border: 1px solid #e8e6dc; /* Border Warm */
  border-radius: 4px; padding: 1px 5px;
  font-size: 13px; font-family: "SF Mono","Fira Code",monospace;
  color: #c96442; /* Terracotta */
}
.prose :deep(pre) {
  background: #f5f4ed; border: 1px solid #e8e6dc;
  border-radius: 8px; padding: 16px 18px;
  overflow-x: auto; margin-bottom: 16px;
  /* Whisper shadow */
  box-shadow: rgba(0,0,0,0.05) 0px 4px 24px;
}
.prose :deep(pre code) { background: none; border: none; padding: 0; color: #4d4c48; }
.prose :deep(blockquote) {
  border-left: 3px solid #c96442; /* Terracotta */
  padding-left: 14px; color: #5e5d59; /* Olive Gray */
  font-style: italic; margin-bottom: 14px;
}

.diff-view {
  font-family: "SF Mono","Fira Code",monospace;
  font-size: 13px; color: #5e5d59; /* Olive Gray */
  white-space: pre-wrap; line-height: 1.60;
}

/* ─── Review pane ──────────────────────────────── */
.review-pane {
  border-top: 1px solid #f0eee6; /* Border Cream */
  background: #faf9f5; /* Ivory */
  padding: 16px 20px; max-height: 38vh;
  overflow-y: auto; flex-shrink: 0;
}

.review-score-row { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
.review-score-label {
  font-size: 12px; font-weight: 500; color: #5e5d59; /* Olive Gray */
  white-space: nowrap; letter-spacing: 0.12px;
}
.review-track { flex: 1; height: 4px; background: #e8e6dc; border-radius: 999px; overflow: hidden; }
.review-fill  {
  height: 100%;
  background: linear-gradient(90deg, #c96442, #d97757); /* Terracotta → Coral */
  border-radius: 999px; transition: width 0.6s cubic-bezier(.4,0,.2,1);
}
.review-score-num {
  font-size: 15px; font-weight: 600;
  color: #c96442; /* Terracotta */
  width: 26px; text-align: right;
}

.sug-list { display: flex; flex-direction: column; gap: 8px; }
.sug-card {
  background: #faf9f5; /* Ivory */
  border: 1px solid #f0eee6; /* Border Cream */
  border-radius: 8px; padding: 12px 14px;
}
.sug-row  { display: flex; align-items: center; gap: 6px; margin-bottom: 4px; }

.sev { font-size: 10px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; border-radius: 4px; padding: 1px 6px; }
.sev.high   { background: #fee2e2; color: #b53333; }
.sev.medium { background: #fef3c7; color: #92400e; }
.sev.low    { background: #dcfce7; color: #166534; }

.sug-prob { font-size: 13px; font-weight: 600; color: #141413; letter-spacing: -0.01em; }
.sug-body { font-size: 13px; color: #5e5d59; /* Olive Gray */ line-height: 1.60; margin: 3px 0 9px; }
.sug-btns { display: flex; gap: 5px; }

.sug-btn {
  border: 1px solid #e8e6dc; /* Border Warm */
  background: none; border-radius: 999px;
  padding: 3px 12px; font-size: 12px;
  color: #5e5d59; /* Olive Gray */
  cursor: pointer; font-family: inherit; transition: all .12s;
}
.sug-btn:hover    { border-color: #c96442; color: #c96442; }
.sug-btn.on       { background: #faf9f5; border-color: #c96442; color: #c96442; }
.sug-btn.red:hover  { border-color: #b53333; color: #b53333; }
.sug-btn.red.on     { background: #fef2f2; border-color: #b53333; color: #b53333; }

.sug-edit {
  width: 100%; margin-top: 8px;
  background: #f5f4ed; /* Parchment */
  border: 1px solid #e8e6dc; /* Border Warm */
  border-radius: 8px; color: #141413; padding: 7px 10px; font-size: 13px;
  font-family: inherit; resize: none; outline: none; transition: border-color .15s;
}
.sug-edit:focus { border-color: #3898ec; } /* Focus Blue — the only cool color */

.review-submit-btn {
  width: 100%; margin-top: 13px;
  background: #c96442; /* Terracotta */
  border: none; border-radius: 8px;
  color: #faf9f5; /* Ivory */
  padding: 9px; font-size: 13px; font-weight: 600;
  font-family: inherit; cursor: pointer; transition: background .15s;
}
.review-submit-btn:hover { background: #a8522d; }

/* ═══════════════════════════════════════════
   RESIZE HANDLE
═══════════════════════════════════════════ */
.resize-handle {
  width: 5px; flex-shrink: 0;
  background: #f0eee6; /* Border Cream */
  cursor: ew-resize;
  transition: background 0.15s; position: relative;
}
.resize-handle::after {
  content: ""; position: absolute; inset: 0 -4px; /* wider hit area */
}
.resize-handle:hover,
.resize-handle.active { background: #c96442; } /* Terracotta */

/* ═══════════════════════════════════════════
   RIGHT — Chat panel
═══════════════════════════════════════════ */
.chat-panel {
  display: flex; flex-direction: column;
  flex-shrink: 0; min-width: 300px; max-width: 720px;
  background: #f5f4ed; /* Parchment */
  overflow: hidden;
}

/* ── Top bar ── */
.chat-topbar {
  display: flex; align-items: center; justify-content: center;
  height: 52px;
  border-bottom: 1px solid #f0eee6; /* Border Cream */
  background: #f5f4ed; /* Parchment */
  flex-shrink: 0; padding: 0 20px;
}

.model-chip { display: flex; align-items: center; gap: 9px; }

.claude-logo {
  width: 28px; height: 28px;
  display: flex; align-items: center; justify-content: center;
  overflow: hidden; border-radius: 7px;
}

.model-name {
  font-size: 14px; font-weight: 600;
  color: #141413; /* Near Black */
  letter-spacing: -0.02em;
}

.model-status {
  font-size: 11px; font-weight: 500; border-radius: 999px;
  padding: 2px 8px; letter-spacing: 0.12px; transition: all .2s;
}
.model-status.ok      { background: #d1fae5; color: #065f46; }
.model-status.loading {
  background: #e8e6dc; /* Warm Sand */
  color: #4d4c48; /* Charcoal Warm */
  animation: pulse-status 1.4s ease-in-out infinite;
}
.model-status.err     { background: #fee2e2; color: #b53333; }

@keyframes pulse-status {
  0%,100% { opacity: 1; }
  50%      { opacity: 0.45; }
}

/* ── Scroll area ── */
.messages-scroll {
  flex: 1; overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: #d1cfc5 transparent; /* Ring Warm */
  mask-image: linear-gradient(to bottom, transparent 0px, #000 20px);
  -webkit-mask-image: linear-gradient(to bottom, transparent 0px, #000 20px);
}

.messages-inner {
  padding: 24px 24px 12px;
  display: flex; flex-direction: column; min-height: 100%;
}

/* ── Welcome ── */
.welcome {
  display: flex; flex-direction: column;
  align-items: center; text-align: center;
  padding: 36px 20px 28px; gap: 8px;
}

.welcome-mark { margin-bottom: 6px; }

.welcome-h {
  /* Anthropic Serif → Georgia */
  font-family: Georgia, "Noto Serif SC", serif;
  font-size: 25.6px; font-weight: 500; /* Single-weight serif */
  line-height: 1.20; letter-spacing: -0.01em;
  color: #141413; /* Near Black */
}

.welcome-p {
  font-size: 15px; color: #5e5d59; /* Olive Gray */
  max-width: 270px; line-height: 1.60; margin-top: 4px;
}

.chips {
  display: flex; flex-direction: column; gap: 6px;
  width: 100%; max-width: 340px; margin-top: 16px;
}

.chip {
  /* White Surface button style */
  background: #faf9f5; /* Ivory */
  border: 1px solid #f0eee6; /* Border Cream */
  /* Ring shadow Level 2 */
  box-shadow: #faf9f5 0px 0px 0px 0px, #d1cfc5 0px 0px 0px 1px;
  border-radius: 12px; padding: 11px 15px;
  font-size: 14px; font-weight: 400;
  color: #4d4c48; /* Charcoal Warm */
  cursor: pointer; font-family: inherit; text-align: left;
  line-height: 1.45; transition: box-shadow .12s, background .12s;
}
.chip:hover {
  background: #ffffff;
  box-shadow: #ffffff 0px 0px 0px 0px, #c96442 0px 0px 0px 1px; /* Terracotta ring */
}
.chip:active { transform: scale(.99); }

/* ── Error ── */
.error-banner {
  display: flex; align-items: center; gap: 7px;
  background: #fef2f2; border: 1px solid #fecaca;
  border-radius: 8px; padding: 11px 14px;
  font-size: 13.5px; color: #b53333; margin: 4px 0;
}

/* ── Boot spinner ── */
.boot-spinner {
  display: flex; justify-content: center; gap: 5px;
  padding: 28px; align-items: center;
}
.boot-spinner span {
  width: 7px; height: 7px; border-radius: 50%;
  background: #b0aea5; /* Warm Silver */
  animation: bounce-dot 1.2s ease-in-out infinite;
}
.boot-spinner span:nth-child(2) { animation-delay: .18s; }
.boot-spinner span:nth-child(3) { animation-delay: .36s; }

/* ── Message list ── */
.msg-list { display: flex; flex-direction: column; }

/* User bubble */
.user-row {
  display: flex; justify-content: flex-end;
  padding: 4px 0 14px;
}

.user-bubble {
  background: #faf9f5; /* Ivory */
  color: #141413;      /* Near Black */
  border: 1px solid #e8e6dc; /* Border Warm */
  box-shadow: #faf9f5 0px 0px 0px 0px, #d1cfc5 0px 0px 0px 1px;
  border-radius: 18px 18px 4px 18px;
  padding: 10px 16px;
  font-size: 15px; font-weight: 400;
  line-height: 1.60; white-space: pre-wrap;
  max-width: 86%; letter-spacing: -0.01em;
}
.user-bubble :deep(p) {
  margin: 0 0 8px;
}
.user-bubble :deep(p:last-child) {
  margin-bottom: 0;
}

/* Assistant row */
.ai-row {
  display: flex; gap: 10px;
  padding: 4px 0 18px; align-items: flex-start;
}

.ai-avatar {
  width: 22px; height: 22px; border-radius: 5px;
  background: transparent;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0; margin-top: 3px; overflow: hidden;
}

.ai-body { display: flex; flex-direction: column; gap: 1px; flex: 1; }
.ai-name {
  font-size: 10px; font-weight: 400;
  color: #87867f; /* Stone Gray */
  letter-spacing: 0.5px; text-transform: uppercase;
  margin-bottom: 3px;
}
.ai-text {
  font-size: 15px; line-height: 1.75;
  color: #141413; /* Near Black */
  letter-spacing: -0.008em;
}
.ai-text :deep(p) {
  margin: 0 0 8px;
}
.ai-text :deep(p:last-child) {
  margin-bottom: 0;
}
.ai-text :deep(ul),
.ai-text :deep(ol) {
  margin: 4px 0 8px 18px;
  padding: 0;
}
.ai-text :deep(li) {
  margin: 0 0 4px;
}
.ai-text :deep(code) {
  font-family: "SF Mono","Fira Code",monospace;
  font-size: 12px;
  background: #f0eee6;
  border-radius: 4px;
  padding: 1px 4px;
}
.ai-text :deep(br) {
  line-height: 1.75;
}

/* ── Retract button ── */
.retract-btn {
  display: flex; align-items: center; gap: 4px;
  background: none; border: none;
  font-size: 11.5px; font-weight: 500;
  color: #87867f; /* Stone Gray */
  font-family: inherit; cursor: pointer;
  padding: 3px 6px; border-radius: 6px;
  align-self: flex-end; margin-right: 4px; margin-bottom: 2px;
  opacity: 0; transition: opacity .15s, color .12s;
}
.user-row:hover .retract-btn { opacity: 1; }
.retract-btn:hover { color: #c96442; }

/* ── Thinking indicator ── */
.thinking-indicator {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 0;
}
.thinking-orb {
  width: 18px; height: 18px; border-radius: 50%;
  border: 2px solid #e8e6dc; /* Warm Sand */
  border-top-color: #c96442; /* Terracotta */
  animation: rot .9s linear infinite;
  flex-shrink: 0;
}
.thinking-label {
  font-size: 14px; color: #87867f; /* Stone Gray */
  letter-spacing: -0.005em;
}

/* ── Typing indicator ── */
.typing {
  display: flex; gap: 4px; align-items: center; height: 22px; padding: 4px 0;
}
.typing span {
  width: 6px; height: 6px; border-radius: 50%;
  background: #b0aea5; /* Warm Silver */
  animation: bounce-dot 1.2s ease-in-out infinite;
}
.typing span:nth-child(2) { animation-delay: .2s; }
.typing span:nth-child(3) { animation-delay: .4s; }

@keyframes bounce-dot {
  0%,80%,100% { transform: translateY(0);    opacity: .3; }
  40%          { transform: translateY(-5px); opacity: 1;  }
}

/* ─── Composer ─────────────────────────────────── */
.composer-area {
  padding: 8px 16px 20px;
  background: #f5f4ed; /* Parchment */
  flex-shrink: 0;
}

.composer-box {
  display: flex; align-items: flex-end; gap: 8px;
  background: #ffffff; /* Pure White */
  border: 1px solid #e8e6dc; /* Border Warm */
  border-radius: 12px; /* Generously rounded */
  padding: 10px 10px 10px 16px;
  /* Whisper shadow Level 3 */
  box-shadow: rgba(0,0,0,0.05) 0px 4px 24px,
              #ffffff 0px 0px 0px 0px, #e8e6dc 0px 0px 0px 1px;
  transition: border-color .15s, box-shadow .15s;
}

.composer-box:focus-within {
  border-color: #3898ec; /* Focus Blue — the only cool color */
  box-shadow: rgba(0,0,0,0.05) 0px 4px 24px,
              #ffffff 0px 0px 0px 0px, #3898ec 0px 0px 0px 1px;
}

.composer-ta {
  flex: 1; border: none; background: none;
  resize: none; outline: none;
  font-size: 15px; line-height: 1.60;
  color: #141413; /* Near Black */
  font-family: inherit;
  height: 24px; max-height: 180px; overflow-y: auto;
  letter-spacing: -0.01em;
}

.composer-ta::placeholder { color: #87867f; } /* Stone Gray */
.composer-ta:disabled     { opacity: .4; cursor: not-allowed; }

/* Send button — Terracotta Brand style */
.send-btn {
  width: 34px; height: 34px; min-width: 34px;
  border-radius: 8px; border: none;
  background: #e8e6dc; /* Warm Sand — inactive */
  color: #4d4c48; /* Charcoal Warm */
  cursor: pointer; display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  transition: background .14s, color .14s, transform .1s, box-shadow .14s;
}

.send-btn.active {
  background: #c96442; /* Terracotta Brand */
  color: #faf9f5;      /* Ivory */
  /* Ring shadow */
  box-shadow: #c96442 0px 0px 0px 0px, #c96442 0px 0px 0px 1px;
}

.send-btn.active:hover {
  background: #a8522d;
  box-shadow: #a8522d 0px 0px 0px 0px, #a8522d 0px 0px 0px 1px;
}
.send-btn.active:active { transform: scale(.92); }
.send-btn:disabled      { cursor: default; }
.send-btn:focus-visible { outline: 2px solid #3898ec; outline-offset: 2px; }

/* Spinner */
.spin {
  width: 13px; height: 13px;
  border: 2px solid rgba(250,249,245,.3);
  border-top-color: #faf9f5;
  border-radius: 50%; animation: rot .65s linear infinite;
}

@keyframes rot { to { transform: rotate(360deg); } }

.composer-hint {
  font-size: 11px; color: #87867f; /* Stone Gray */
  text-align: center; margin-top: 8px; letter-spacing: 0.5px;
}

/* ═══════════════════════════════════════════
   TRANSITIONS
═══════════════════════════════════════════ */
.t-msg-enter-active { animation: msg-in .2s cubic-bezier(0.16, 1, 0.3, 1); }
@keyframes msg-in {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

.t-welcome-enter-active { animation: welcome-in .3s cubic-bezier(0.16, 1, 0.3, 1); }
.t-welcome-leave-active { animation: welcome-out .18s ease-in forwards; }
@keyframes welcome-in  { from { opacity: 0; transform: translateY(10px) scale(.98); } to { opacity: 1; transform: none; } }
@keyframes welcome-out { to   { opacity: 0; transform: scale(.97); } }

.t-fade-enter-active { transition: opacity .16s ease; }
.t-fade-leave-active { transition: opacity .12s ease; }
.t-fade-enter-from, .t-fade-leave-to { opacity: 0; }

.t-slide-enter-active { animation: slide-up .22s cubic-bezier(0.16, 1, 0.3, 1); }
.t-slide-leave-active { animation: slide-down .16s ease-in forwards; }
@keyframes slide-up   { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
@keyframes slide-down { to   { opacity: 0; transform: translateY(6px); } }

.t-icon-enter-active { animation: icon-pop .14s cubic-bezier(0.16,1,0.3,1); }
.t-icon-leave-active { animation: icon-pop .1s ease-in reverse forwards; }
@keyframes icon-pop { from { opacity: 0; transform: scale(.7); } to { opacity: 1; transform: scale(1); } }

/* ═══════════════════════════════════════════
   RESPONSIVE
═══════════════════════════════════════════ */
@media (max-width: 860px) {
  .shell { flex-direction: column; }
  .artifact-panel { flex: 1; min-width: unset; }
  .resize-handle { width: 100%; height: 5px; cursor: ns-resize; }
  .chat-panel { width: 100% !important; max-width: unset; }
}
</style>
