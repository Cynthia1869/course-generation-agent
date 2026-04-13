const API_BASE = "http://localhost:8000/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  const json = await response.json();
  return json.data as T;
}

export async function createThread() {
  return request<{ thread: { thread_id: string } }>("/threads", { method: "POST" });
}

export async function fetchThread(threadId: string) {
  return request<{ state: any }>(`/threads/${threadId}`);
}

export async function sendMessage(threadId: string, content: string) {
  return request(`/threads/${threadId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export async function fetchLatestArtifact(threadId: string) {
  return request<{ artifact: any }>(`/threads/${threadId}/artifacts/latest`);
}

export async function fetchDiff(threadId: string, version: number, prevVersion: number) {
  return request<{ diff: string }>(`/threads/${threadId}/artifacts/${version}/diff/${prevVersion}`);
}

export async function submitReview(threadId: string, batchId: string, reviewActions: any[]) {
  return request(`/threads/${threadId}/review-batches/${batchId}/submit`, {
    method: "POST",
    body: JSON.stringify({
      submitter_id: "default-user",
      review_actions: reviewActions,
    }),
  });
}

export function streamThread(threadId: string, onEvent: (event: MessageEvent, type: string) => void) {
  const source = new EventSource(`${API_BASE}/threads/${threadId}/stream`);
  ["assistant_message", "token_stream", "node_update", "review_batch", "artifact_updated", "audit_event", "file_uploaded"].forEach(
    (type) => {
      source.addEventListener(type, (event) => onEvent(event as MessageEvent, type));
    },
  );
  return source;
}
