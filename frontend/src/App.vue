<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { marked } from "marked";

import {
  createThread,
  fetchDiff,
  fetchThread,
  sendMessage,
  streamThread,
  submitReview,
} from "./lib/api";
import type { ReviewBatch, ThreadState } from "./types";

const threadId = ref("");
const threadState = ref<ThreadState | null>(null);
const content = ref("");
const activeTab = ref<"conversation" | "markdown" | "diff">("conversation");
const diffText = ref("");
const reviewDraft = ref<Record<string, { action: "approve" | "edit" | "reject"; edited: string }>>({});
const eventSource = ref<EventSource | null>(null);

const latestReview = computed<ReviewBatch | null>(() => {
  const batches = threadState.value?.review_batches ?? [];
  return batches.length ? batches[batches.length - 1] : null;
});

const markdownHtml = computed(() => marked.parse(threadState.value?.draft_artifact?.markdown ?? ""));

async function bootstrap() {
  const created = await createThread();
  threadId.value = created.thread.thread_id;
  await refreshThread();
  eventSource.value = streamThread(threadId.value, async (event, type) => {
    if (type === "assistant_message") {
      await refreshThread();
    }
    if (type === "artifact_updated" || type === "review_batch" || type === "node_update") {
      await refreshThread();
    }
  });
}

async function refreshThread() {
  const response = await fetchThread(threadId.value);
  threadState.value = response.state;
  const artifact = threadState.value?.draft_artifact;
  if (artifact && artifact.version > 1) {
    const diff = await fetchDiff(threadId.value, artifact.version, artifact.version - 1);
    diffText.value = diff.diff;
  }
}

async function handleSend() {
  if (!content.value.trim()) return;
  await sendMessage(threadId.value, content.value.trim());
  content.value = "";
}

function setAction(suggestionId: string, action: "approve" | "edit" | "reject") {
  const previous = reviewDraft.value[suggestionId] ?? { action, edited: "" };
  reviewDraft.value[suggestionId] = { ...previous, action };
}

async function handleReviewSubmit() {
  if (!latestReview.value) return;
  const reviewActions = latestReview.value.suggestions.map((suggestion) => {
    const draft = reviewDraft.value[suggestion.suggestion_id] ?? { action: "approve", edited: "" };
    return {
      suggestion_id: suggestion.suggestion_id,
      action: draft.action,
      edited_suggestion: draft.edited || undefined,
      reviewer_id: "default-user",
      comment: "",
    };
  });
  await submitReview(threadId.value, latestReview.value.review_batch_id, reviewActions);
}

onMounted(bootstrap);
</script>

<template>
  <div class="app-shell">
    <aside class="left-panel">
      <div class="tab-bar">
        <button :class="{ active: activeTab === 'conversation' }" @click="activeTab = 'conversation'">对话</button>
        <button :class="{ active: activeTab === 'markdown' }" @click="activeTab = 'markdown'">当前稿</button>
        <button :class="{ active: activeTab === 'diff' }" @click="activeTab = 'diff'">对比</button>
      </div>
      <div v-if="activeTab === 'conversation'" class="conversation">
        <div
          v-for="message in threadState?.messages ?? []"
          :key="message.message_id"
          class="message"
          :class="message.role"
        >
          <div class="role">{{ message.role === "user" ? "你" : "系统" }}</div>
          <div class="bubble">{{ message.content }}</div>
        </div>
      </div>
      <div v-else-if="activeTab === 'markdown'" class="markdown-panel">
        <div class="markdown-render" v-html="markdownHtml"></div>
      </div>
      <pre v-else class="diff-panel">{{ diffText || "当前还没有上一版差异。" }}</pre>
    </aside>

    <main class="center-panel">
      <div class="hero-copy">
        <h1>制课生成 Agent</h1>
        <p>从真实对话开始，补全需求、生成主稿、评审改稿，直到通过。</p>
      </div>
      <div class="composer">
        <textarea
          v-model="content"
          placeholder="在这里描述你的课程需求，系统会继续追问并逐步生成内容。"
          rows="6"
        />
        <button class="send" @click="handleSend">发送</button>
      </div>
    </main>

    <aside class="right-panel">
      <div class="review-header">
        <h2>评分与审核</h2>
        <div v-if="latestReview" class="score">总分 {{ latestReview.total_score }}</div>
      </div>
      <div v-if="latestReview" class="review-body">
        <section class="criteria">
          <h3>分项得分</h3>
          <div v-for="criterion in latestReview.criteria" :key="criterion.criterion_id" class="criterion">
            <strong>{{ criterion.name }}</strong>
            <span>{{ criterion.score }} / {{ criterion.max_score }}</span>
            <p>{{ criterion.reason }}</p>
          </div>
        </section>
        <section class="suggestions">
          <h3>逐条审核</h3>
          <div v-for="suggestion in latestReview.suggestions" :key="suggestion.suggestion_id" class="suggestion">
            <div class="problem">{{ suggestion.problem }}</div>
            <div class="suggestion-text">{{ suggestion.suggestion }}</div>
            <div class="evidence">证据片段：{{ suggestion.evidence_span }}</div>
            <div class="actions">
              <button @click="setAction(suggestion.suggestion_id, 'approve')">通过</button>
              <button @click="setAction(suggestion.suggestion_id, 'edit')">编辑</button>
              <button @click="setAction(suggestion.suggestion_id, 'reject')">驳回</button>
            </div>
            <textarea
              v-if="reviewDraft[suggestion.suggestion_id]?.action === 'edit'"
              v-model="reviewDraft[suggestion.suggestion_id].edited"
              rows="3"
              placeholder="输入人工确认后的修改意见"
            />
          </div>
        </section>
        <button class="submit-review" @click="handleReviewSubmit">提交人工审核结果</button>
      </div>
      <div v-else class="review-empty">当前还没有评分结果。</div>
    </aside>
  </div>
</template>

<style scoped>
:global(body) {
  margin: 0;
  font-family: "PingFang SC", "Noto Sans SC", sans-serif;
  background:
    radial-gradient(circle at top, rgba(34, 197, 94, 0.18), transparent 35%),
    linear-gradient(180deg, #f5f7fb 0%, #eef2f7 100%);
  color: #1f2937;
}

.app-shell {
  display: grid;
  grid-template-columns: 1.2fr 1fr 0.9fr;
  min-height: 100vh;
}

.left-panel,
.right-panel {
  border-right: 1px solid rgba(148, 163, 184, 0.25);
  background: rgba(255, 255, 255, 0.84);
  backdrop-filter: blur(16px);
}

.right-panel {
  border-right: none;
  border-left: 1px solid rgba(148, 163, 184, 0.25);
}

.tab-bar {
  display: flex;
  gap: 8px;
  padding: 16px;
}

.tab-bar button {
  border: none;
  border-radius: 999px;
  padding: 10px 14px;
  background: #e5e7eb;
  cursor: pointer;
}

.tab-bar button.active {
  background: #111827;
  color: white;
}

.conversation,
.markdown-panel,
.diff-panel,
.review-body {
  padding: 16px;
  overflow: auto;
  height: calc(100vh - 92px);
}

.message {
  margin-bottom: 16px;
}

.role {
  font-size: 12px;
  opacity: 0.6;
  margin-bottom: 6px;
}

.bubble {
  padding: 14px 16px;
  border-radius: 18px;
  background: white;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.08);
  line-height: 1.6;
}

.message.user .bubble {
  background: #dbeafe;
}

.center-panel {
  display: grid;
  place-items: center;
  padding: 32px;
}

.hero-copy {
  max-width: 520px;
  text-align: center;
}

.hero-copy h1 {
  font-size: 42px;
  margin-bottom: 12px;
}

.hero-copy p {
  line-height: 1.7;
  color: #4b5563;
}

.composer {
  width: min(560px, 100%);
  margin-top: 24px;
  padding: 20px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 20px 45px rgba(15, 23, 42, 0.12);
}

.composer textarea {
  width: 100%;
  border: none;
  outline: none;
  resize: vertical;
  background: transparent;
  font-size: 15px;
  line-height: 1.7;
}

.send,
.submit-review {
  margin-top: 16px;
  width: 100%;
  border: none;
  border-radius: 14px;
  background: #0f172a;
  color: white;
  padding: 12px 16px;
  cursor: pointer;
}

.review-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.25);
}

.criterion,
.suggestion {
  padding: 12px;
  border-radius: 16px;
  background: white;
  box-shadow: 0 12px 26px rgba(15, 23, 42, 0.06);
  margin-bottom: 12px;
}

.actions {
  display: flex;
  gap: 8px;
  margin: 12px 0;
}

.actions button {
  border: 1px solid #cbd5e1;
  background: transparent;
  border-radius: 999px;
  padding: 8px 12px;
  cursor: pointer;
}

.review-empty {
  padding: 16px;
  color: #64748b;
}

.diff-panel {
  white-space: pre-wrap;
}

@media (max-width: 1200px) {
  .app-shell {
    grid-template-columns: 1fr;
  }

  .left-panel,
  .right-panel {
    border: none;
  }

  .center-panel {
    order: -1;
  }
}
</style>
