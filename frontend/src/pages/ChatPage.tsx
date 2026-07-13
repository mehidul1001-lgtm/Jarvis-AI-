import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import { chatApi } from '../api/endpoints';
import { Markdown } from '../components/Markdown';
import { Badge, Button, ErrorBanner, Spinner } from '../components/ui';
import { realtimeBus } from '../hooks/realtimeBus';
import type { ChatMessage, Conversation } from '../types';

interface ToolActivity {
  name: string;
  status: string;
}

export function ChatPage() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [streamText, setStreamText] = useState('');
  const [toolActivity, setToolActivity] = useState<ToolActivity[]>([]);
  const [error, setError] = useState('');
  const [loadingThread, setLoadingThread] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const activeIdRef = useRef<string | null>(null);
  activeIdRef.current = activeId;

  const refreshConversations = useCallback(async () => {
    const page = await chatApi.listConversations();
    setConversations(page.items);
    return page.items;
  }, []);

  useEffect(() => {
    refreshConversations().catch(() => setError('Failed to load conversations'));
  }, [refreshConversations]);

  useEffect(() => {
    if (!activeId) return;
    setLoadingThread(true);
    chatApi
      .messages(activeId)
      .then(setMessages)
      .catch(() => setError('Failed to load messages'))
      .finally(() => setLoadingThread(false));
  }, [activeId]);

  // Streaming events for the active conversation.
  useEffect(() => {
    return realtimeBus.subscribe((event) => {
      if (event.conversation_id !== activeIdRef.current) return;
      if (event.type === 'chat.delta' && typeof event.text === 'string') {
        setStreamText((current) => current + event.text);
      } else if (event.type === 'chat.tool') {
        setToolActivity((current) => {
          const next = current.filter((tool) => tool.name !== event.name);
          return [...next, { name: String(event.name), status: String(event.status) }];
        });
      } else if (event.type === 'chat.status' && event.status === 'revising') {
        setStreamText('');
        setToolActivity([{ name: 'self-review', status: 'running' }]);
      }
    });
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamText, toolActivity]);

  const openConversation = (id: string) => {
    setActiveId(id);
    setMessages([]);
    setStreamText('');
    setToolActivity([]);
    setSidebarOpen(false);
  };

  const newConversation = async () => {
    try {
      const conversation = await chatApi.createConversation();
      await refreshConversations();
      openConversation(conversation.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to create conversation');
    }
  };

  const removeConversation = async (id: string) => {
    await chatApi.deleteConversation(id);
    if (activeId === id) {
      setActiveId(null);
      setMessages([]);
    }
    await refreshConversations();
  };

  const send = async (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || sending) return;

    let conversationId = activeId;
    setError('');
    setSending(true);
    try {
      if (!conversationId) {
        const conversation = await chatApi.createConversation();
        conversationId = conversation.id;
        setActiveId(conversationId);
      }
      setDraft('');
      setMessages((current) => [
        ...current,
        {
          id: `local-${Date.now()}`,
          role: 'user',
          content: text,
          created_at: new Date().toISOString(),
          meta: { confidence: null, latency_ms: null, tool_calls: [], input_tokens: null, output_tokens: null, revised: false },
        },
      ]);
      setStreamText('');
      setToolActivity([]);

      const assistant = await chatApi.send(conversationId, text);
      setStreamText('');
      setToolActivity([]);
      setMessages((current) => [...current, assistant]);
      await refreshConversations();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Message failed — try again');
      setStreamText('');
      setToolActivity([]);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex h-full gap-4">
      {/* Conversation list */}
      <aside
        className={`${sidebarOpen ? 'block' : 'hidden'} w-full shrink-0 md:block md:w-64`}
      >
        <div className="card flex h-full flex-col p-3">
          <Button onClick={newConversation} style={{ width: '100%' }}>
            + New chat
          </Button>
          <div className="mt-3 flex-1 space-y-1 overflow-y-auto">
            {conversations.map((conversation) => (
              <div
                key={conversation.id}
                className={`group flex cursor-pointer items-center justify-between rounded-lg px-3 py-2 text-sm ${
                  conversation.id === activeId
                    ? 'bg-brand-600/10 text-brand-600 dark:bg-brand-500/15 dark:text-brand-300'
                    : 'hover:bg-slate-100 dark:hover:bg-slate-800'
                }`}
                onClick={() => openConversation(conversation.id)}
              >
                <span className="truncate">{conversation.title}</span>
                <button
                  type="button"
                  aria-label={`Delete ${conversation.title}`}
                  className="hidden text-slate-400 hover:text-red-500 group-hover:block"
                  onClick={(clickEvent) => {
                    clickEvent.stopPropagation();
                    void removeConversation(conversation.id);
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
            {conversations.length === 0 && (
              <p className="px-3 py-6 text-center text-xs text-slate-400">
                No conversations yet
              </p>
            )}
          </div>
        </div>
      </aside>

      {/* Thread */}
      <section className={`${sidebarOpen ? 'hidden' : 'flex'} min-w-0 flex-1 flex-col md:flex`}>
        <div className="card flex h-full flex-col">
          <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3 dark:border-slate-800 md:hidden">
            <Button variant="secondary" onClick={() => setSidebarOpen(true)}>
              Conversations
            </Button>
          </header>

          <div className="flex-1 space-y-4 overflow-y-auto p-4">
            {error && <ErrorBanner message={error} />}
            {loadingThread && (
              <div className="flex justify-center py-8">
                <Spinner />
              </div>
            )}
            {!activeId && !loadingThread && messages.length === 0 && (
              <div className="flex h-full flex-col items-center justify-center text-center">
                <span className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-600/10 text-2xl">
                  💬
                </span>
                <p className="font-semibold">Talk to JARVIS</p>
                <p className="mt-1 max-w-sm text-sm text-slate-400">
                  Ask about your business, store facts to memory, or queue work for the
                  agents. Type below to start.
                </p>
              </div>
            )}
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}

            {sending && (
              <div className="max-w-3xl">
                {toolActivity.length > 0 && (
                  <div className="mb-2 flex flex-wrap gap-2">
                    {toolActivity.map((tool) => (
                      <Badge
                        key={tool.name}
                        color={
                          tool.status === 'error'
                            ? 'red'
                            : tool.status === 'done'
                              ? 'green'
                              : 'amber'
                        }
                      >
                        🔧 {tool.name} {tool.status === 'running' ? '…' : `(${tool.status})`}
                      </Badge>
                    ))}
                  </div>
                )}
                <div className="rounded-2xl rounded-tl-sm bg-slate-100 p-4 dark:bg-slate-800">
                  {streamText ? (
                    <Markdown text={streamText} />
                  ) : (
                    <Spinner className="h-4 w-4" />
                  )}
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          <form
            onSubmit={send}
            className="flex gap-2 border-t border-slate-200 p-3 dark:border-slate-800"
          >
            <input
              className="input"
              placeholder="Message JARVIS…"
              value={draft}
              onChange={(changeEvent) => setDraft(changeEvent.target.value)}
              disabled={sending}
              aria-label="Chat message"
            />
            <Button type="submit" loading={sending} disabled={!draft.trim()}>
              Send
            </Button>
          </form>
        </div>
      </section>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-3xl rounded-2xl p-4 ${
          isUser
            ? 'rounded-tr-sm bg-brand-600 text-white'
            : 'rounded-tl-sm bg-slate-100 dark:bg-slate-800'
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap text-sm">{message.content}</p>
        ) : (
          <>
            <Markdown text={message.content} />
            <MessageFooter message={message} />
          </>
        )}
      </div>
    </div>
  );
}

function MessageFooter({ message }: { message: ChatMessage }) {
  const { meta } = message;
  const hasInfo =
    meta.confidence !== null || meta.tool_calls.length > 0 || meta.revised;
  if (!hasInfo) return null;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-200 pt-2 text-xs text-slate-400 dark:border-slate-700">
      {meta.confidence !== null && (
        <span title="Self-assessed confidence">
          confidence {(meta.confidence * 100).toFixed(0)}%
        </span>
      )}
      {meta.tool_calls.length > 0 && <span>tools: {meta.tool_calls.join(', ')}</span>}
      {meta.revised && <Badge color="amber">self-corrected</Badge>}
      {meta.latency_ms !== null && <span>{(meta.latency_ms / 1000).toFixed(1)}s</span>}
    </div>
  );
}
