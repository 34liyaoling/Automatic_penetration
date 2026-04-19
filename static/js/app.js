let ws = null;
let tasks = {};
let streamContents = {};
let activeTaskId = null;
let collapsedStream = {};
let isSubmitting = false;
let runningTaskId = null;
let conversations = {};
let currentConversationId = null;
let contextSettings = {
    contextLength: 10,
    includeResults: 'full'
};
let sidebarOpen = true;

let processedStreamSteps = new Set();
let createdReportContainers = new Set();
let executedTasks = new Set();

function toggleSidebar() {
    sidebarOpen = !sidebarOpen;
    const sidebar = document.getElementById('conversationSidebar');
    const mainContent = document.getElementById('mainContent');
    const toggleBtn = document.getElementById('toggleSidebarBtn');

    if (sidebarOpen) {
        sidebar.classList.remove('sidebar-hidden');
        mainContent.classList.remove('ml-0');
        mainContent.classList.add('ml-72');
        toggleBtn.classList.remove('rotated');
    } else {
        sidebar.classList.add('sidebar-hidden');
        mainContent.classList.remove('ml-72');
        mainContent.classList.add('ml-0');
        toggleBtn.classList.add('rotated');
    }
}

async function createNewConversation() {
    const newConvId = 'conv_' + Date.now();
    const localConv = {
        id: newConvId,
        name: '新对话',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        messages: [],
        task_ids: []
    };

    conversations[newConvId] = localConv;

    try {
        const response = await fetch('/api/v1/conversations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ conversation_id: newConvId, name: '新对话' })
        });
        const data = await response.json();
        if (data.success && data.conversation) {
            conversations[newConvId] = data.conversation;
        }
    } catch (e) {
        console.error('Failed to create conversation:', e);
    }

    switchConversation(newConvId);
    renderConversationList();
}

function renderConversationMessages(conv) {
    const chatContainer = document.getElementById('chatContainer');
    let html = '';

    if (conv.messages && conv.messages.length > 0) {
        for (let i = 0; i < conv.messages.length; i++) {
            const msg = conv.messages[i];
            if (msg.role === 'user') {
                html += `
                    <div class="flex justify-end">
                        <div class="user-message text-white rounded-2xl rounded-tr-sm px-5 py-4 max-w-[80%]">
                            <p>${escapeHtml(msg.content)}</p>
                        </div>
                    </div>
                `;
                if (msg.task_data && msg.task_data.id) {
                    html += `<div id="task-placeholder-${msg.task_data.id}"></div>`;
                }
            } else if (msg.role === 'system' && msg.task_data) {
                html += `<div id="task-placeholder-${msg.task_data.id}"></div>`;
            }
        }
    }

    if (!html) {
        addWelcomeMessage();
    } else {
        chatContainer.innerHTML = html;
    }
}

function switchConversation(convId) {
    currentConversationId = convId;
    
    executedTasks.clear();
    streamContents = {};
    processedStreamSteps.clear();
    createdReportContainers.clear();
    
    const conv = conversations[convId];
    if (conv) {
        renderConversationMessages(conv);

        if (conv.messages) {
            for (let i = 0; i < conv.messages.length; i++) {
                const msg = conv.messages[i];
                if ((msg.role === 'user' || msg.role === 'system') && msg.task_data) {
                    const taskId = msg.task_data.id;
                    if (taskId) {
                        tasks[taskId] = msg.task_data;
                        const existingDiv = document.getElementById(`message-${taskId}`);
                        if (!existingDiv) {
                            addAIMessage(taskId);
                        }
                        updateTaskMessage(msg.task_data);
                    }
                }
            }
        }

        updateContextInfo();
    }
    renderConversationList();
}

function renderConversationList() {
    const listContainer = document.getElementById('conversationList');
    const convArray = Object.values(conversations).sort((a, b) =>
        new Date(b.updated_at || b.created_at) - new Date(a.updated_at || a.created_at)
    );

    if (convArray.length === 0) {
        listContainer.innerHTML = `
            <div class="text-center text-gray-500 py-8">
                <p class="text-sm">暂无对话记录</p>
            </div>
        `;
        return;
    }

    listContainer.innerHTML = `
        <div class="flex items-center justify-between px-3 py-2 border-b border-gray-200">
            <span class="text-xs text-gray-500">${convArray.length} 个对话</span>
            <button onclick="deleteAllConversations()" class="text-xs text-red-500 hover:text-red-700 hover:bg-red-50 px-2 py-1 rounded transition">
                清空全部
            </button>
        </div>
    ` + convArray.map(conv => {
        const isActive = conv.id === currentConversationId;
        const name = conv.name || '新对话';
        const preview = conv.messages.length > 0 ? conv.messages[conv.messages.length - 1].content.substring(0, 30) + '...' : '暂无消息';
        const time = formatRelativeTime(conv.updated_at || conv.created_at);

        return `
            <div class="conversation-item group p-3 rounded-lg cursor-pointer ${isActive ? 'active' : ''}"
                 onclick="switchConversation('${conv.id}')">
                <div class="flex items-start justify-between">
                    <div class="flex-1 min-w-0">
                        <h4 class="font-semibold text-sm text-gray-800 truncate">${escapeHtml(name)}</h4>
                        <p class="text-xs text-gray-500 truncate mt-1">${escapeHtml(preview)}</p>
                    </div>
                    <button onclick="event.stopPropagation(); deleteConversation('${conv.id}')"
                            class="p-1 hover:bg-red-100 rounded transition opacity-0 group-hover:opacity-100 flex-shrink-0"
                            title="删除对话">
                        <svg class="w-4 h-4 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
                        </svg>
                    </button>
                </div>
                <div class="flex items-center mt-2 text-xs text-gray-400">
                    <span>${time}</span>
                    <span class="mx-1">•</span>
                    <span>${conv.messages.length} 条消息</span>
                </div>
            </div>
        `;
    }).join('');
}

async function deleteAllConversations() {
    if (!confirm('确定要清空所有对话历史吗？此操作不可恢复！')) return;

    const convIds = Object.keys(conversations);
    conversations = {};

    for (const convId of convIds) {
        try {
            await fetch(`/api/v1/conversations/${convId}`, {
                method: 'DELETE'
            });
        } catch (e) {
            console.error('Failed to delete conversation:', e);
        }
    }

    currentConversationId = null;
    createNewConversation();
    renderConversationList();
}

async function deleteConversation(convId) {
    if (!confirm('确定要删除这个对话吗？')) return;

    delete conversations[convId];

    try {
        await fetch(`/api/v1/conversations/${convId}`, {
            method: 'DELETE'
        });
    } catch (e) {
        console.error('Failed to delete conversation:', e);
    }


    if (currentConversationId === convId) {
        const convArray = Object.values(conversations);
        currentConversationId = convArray.length > 0 ? convArray[0].id : null;
        if (currentConversationId) {
            switchConversation(currentConversationId);
        } else {
            createNewConversation();
        }
    }
    renderConversationList();
    saveCurrentConversation();
}

async function clearCurrentConversation() {
    if (!currentConversationId) return;
    const conv = conversations[currentConversationId];
    if (conv) {
        conv.messages = [];
        conv.task_ids = [];

        try {
            await fetch(`/api/v1/conversations/${currentConversationId}/clear`, {
                method: 'POST'
            });
        } catch (e) {
            console.error('Failed to clear conversation:', e);
        }

        updateContextInfo();
        clearChatContainer();
        addWelcomeMessage();
    }
}

function formatRelativeTime(isoString) {
    const date = new Date(isoString);
    const now = new Date();
    const diff = now - date;
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 1) return '刚刚';
    if (minutes < 60) return `${minutes}分钟前`;
    if (hours < 24) return `${hours}小时前`;
    if (days < 7) return `${days}天前`;
    return date.toLocaleDateString('zh-CN');
}

function toggleContextPanel() {
    const panel = document.getElementById('contextPanel');
    panel.classList.toggle('hidden');
}

function updateContextSettings() {
    contextSettings.contextLength = parseInt(document.getElementById('contextLength').value);
    contextSettings.includeResults = document.getElementById('includeResults').value;
    updateContextInfo();
}

function updateContextInfo() {
    const info = document.getElementById('contextInfo');
    const convName = document.getElementById('currentConversationName');
    const countEl = document.getElementById('contextCount');

    if (currentConversationId && conversations[currentConversationId]) {
        info.classList.remove('hidden');
        const conv = conversations[currentConversationId];
        convName.textContent = conv.name || '新对话';
        countEl.textContent = conv.messages.length;
    } else {
        info.classList.add('hidden');
    }
}

async function saveCurrentConversation() {
    if (!currentConversationId || !conversations[currentConversationId]) return;

    const conv = conversations[currentConversationId];
    try {
        await fetch(`/api/v1/conversations/${currentConversationId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: conv.name,
                messages: conv.messages
            })
        });
    } catch (e) {
        console.error('Failed to save to server:', e);
    }
}

async function loadConversationsFromServer() {
    try {
        const response = await fetch('/api/v1/conversations');
        const data = await response.json();
        if (data.success && data.conversations) {
            conversations = {};
            data.conversations.forEach(conv => {
                conversations[conv.id] = conv;
            });
            renderConversationList();
            return conversations;
        }
    } catch (e) {
        console.error('Failed to load from server:', e);
    }
    return {};
}

async function loadConversations() {
    await loadConversationsFromServer();

    if (Object.keys(conversations).length === 0) {
        await createNewConversation();
    } else {
        const convArray = Object.values(conversations);
        convArray.sort((a, b) => new Date(b.updated_at || 0) - new Date(a.updated_at || 0));
        currentConversationId = convArray[0].id;
        switchConversation(currentConversationId);
    }
    renderConversationList();
}

function clearChatContainer() {
    const chatContainer = document.getElementById('chatContainer');
    chatContainer.innerHTML = '';
}

function addWelcomeMessage() {
    const chatContainer = document.getElementById('chatContainer');
    chatContainer.innerHTML = `
        <div class="flex justify-center">
            <div class="bg-blue-50 text-blue-700 px-6 py-3 rounded-full text-sm">
                👋 你好！我是你的智能渗透测试助手
            </div>
        </div>
        <div class="flex items-start space-x-3">
            <div class="w-10 h-10 rounded-full bg-blue-500 flex items-center justify-center flex-shrink-0">
                <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path>
                </svg>
            </div>
            <div class="ai-message rounded-2xl rounded-tl-sm px-5 py-4 max-w-[80%]">
                <p class="text-gray-800">请告诉我你要测试的目标地址，例如：</p>
                <ul class="mt-2 text-sm text-gray-600 space-y-1">
                    <li>• 192.168.1.1</li>
                    <li>• example.com</li>
                    <li>• https://test.example.com</li>
                </ul>
            </div>
        </div>
    `;
}

function initWebSocket() {
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
        return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/v1/ws`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        updateConnectionStatus(true);
    };

    ws.onclose = (event) => {
        updateConnectionStatus(false);
        setTimeout(initWebSocket, 3000);
    };

    ws.onerror = (error) => {
        updateConnectionStatus(false);
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleWebSocketMessage(data);
        } catch (e) {
            console.error('Failed to parse WebSocket message:', e);
        }
    };
}

function updateConnectionStatus(connected) {
    const statusEl = document.getElementById('connectionStatus');
    if (connected) {
        statusEl.innerHTML = `
            <span class="w-2 h-2 rounded-full bg-green-400"></span>
            <span class="text-sm">已连接</span>
        `;
    } else {
        statusEl.innerHTML = `
            <span class="w-2 h-2 rounded-full bg-red-400"></span>
            <span class="text-sm">未连接</span>
        `;
    }
}

function handleWebSocketMessage(data) {
    if (data.type === 'task_progress') {
        tasks[data.task_id] = data.task;

        if (data.task.status === 'completed') {
            showToast('🎉 任务执行完成！');
            fetchTaskDetails(data.task_id);
        } else if (data.task.status === 'failed') {
            showToast('❌ 任务执行失败', 4000);
        } else if (data.task.status === 'cancelled') {
            showToast('⏹️ 任务已终止', 3000);
        }

        updateTaskMessage(data.task);
    } else if (data.type === 'llm_stream') {
        if (data.step === 'command_output') {
            return;
        }

        const stepKey = `${data.task_id}-${data.step}`;

        if (data.content === '[DONE]') {
            const isGeneratingReport = data.step === 'analyze_results';
            let currentThinking = document.getElementById(`thinking-container-${data.task_id}-${data.step}`);
            if (!currentThinking && isGeneratingReport) {
                currentThinking = document.getElementById(`thinking-container-${data.task_id}-生成报告`);
            }
            if (currentThinking) {
                const currentStepType = currentThinking.id.replace(`thinking-container-${data.task_id}-`, '').replace('生成报告', 'analyze_results');
                if (currentStepType !== data.step) {
                    console.warn(`[DONE] Container mismatch: expected ${data.step}, found container with id ${currentThinking.id}, treating as ${currentStepType}`);
                    currentThinking = null;
                }
            }
            if (!currentThinking) {
                const allContainers = document.querySelectorAll(`[id^="thinking-container-${data.task_id}"], [id^="thinking-current-${data.task_id}"]`);
                for (const container of allContainers) {
                    if (container.dataset.step === data.step) {
                        currentThinking = container;
                        break;
                    }
                }
            }
            if (!currentThinking) {
                console.warn(`[DONE] No container found for step ${data.step} in task ${data.task_id}`);
                return;
            }
            const stepNames = {
                'ai_decision': 'AI思考',
                'analyze_results': '结果分析'
            };
            const stepName = isGeneratingReport ? '报告生成' : (stepNames[data.step] || data.step);
            const displayIcon = isGeneratingReport ? '📊' : '💭';

            collapseThinking(currentThinking);

            const header = currentThinking.querySelector('.thinking-header');
            if (header) {
                header.classList.remove('thinking');
                header.classList.add('done');
                header.innerHTML = `
                    <span class="flex items-center space-x-2">
                        <svg class="w-4 h-4 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                        </svg>
                        <span class="font-semibold thinking-text">${displayIcon} ${stepName}完成</span>
                    </span>
                    <svg class="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                    </svg>
                `;
            }

            const contentDiv = currentThinking.querySelector('.thinking-content');
            if (contentDiv) {
                contentDiv.classList.remove('streaming');

                const rawContent = streamContents[data.task_id]?.[data.step] || '';
                console.log(`[DONE] ${data.step} for task ${data.task_id}, content length: ${rawContent.length}`);
                console.log(`[DONE] Full streamContents:`, JSON.stringify(streamContents));
                contentDiv.innerHTML = `<pre class="whitespace-pre-wrap font-mono text-xs">${escapeHtml(rawContent)}</pre>`;
            }

            if (!isGeneratingReport) {
                const reportKey = `report-${data.task_id}`;
                if (!createdReportContainers.has(reportKey)) {
                    createdReportContainers.add(reportKey);
                    showToast(`${stepName}完成，正在生成报告...`);

                    const aiMessage = document.querySelector(`#message-${data.task_id} .ai-message`);
                    if (!aiMessage) {
                        return;
                    }

                    let reportContainer = document.getElementById(`thinking-report-${data.task_id}`);
                    if (reportContainer) {
                        return;
                    }

                    reportContainer = document.createElement('div');
                    reportContainer.id = `thinking-report-${data.task_id}`;
                    reportContainer.className = 'thinking-item bg-white rounded-lg border border-gray-200 overflow-hidden mt-3';
                    reportContainer.innerHTML = `
                        <div class="thinking-header thinking flex items-center justify-between bg-blue-50 px-3 py-2 cursor-pointer"
                             onclick="toggleThinking(this.parentElement)">
                            <span class="flex items-center space-x-2">
                                <svg class="w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                                </svg>
                                <span class="font-semibold text-blue-700">📊 正在生成报告...</span>
                            </span>
                            <svg class="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                            </svg>
                        </div>
                        <div class="thinking-content px-3 py-2 text-sm whitespace-pre-wrap max-h-60 overflow-y-auto">
                            正在分析数据，生成安全报告...
                        </div>
                    `;
                    aiMessage.appendChild(reportContainer);
                }
            } else {
                const reportContainer = document.getElementById(`thinking-report-${data.task_id}`);
                if (reportContainer) {
                    const header = reportContainer.querySelector('.thinking-header');
                    if (header) {
                        header.classList.remove('thinking');
                        header.classList.add('done');
                        header.innerHTML = `
                            <span class="flex items-center space-x-2">
                                <svg class="w-4 h-4 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                                </svg>
                                <span class="font-semibold thinking-text">📊 报告生成完成</span>
                            </span>
                            <svg class="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                            </svg>
                        `;
                    }
                    const contentDiv = reportContainer.querySelector('.thinking-content');
                    if (contentDiv) {
                        contentDiv.classList.remove('streaming');
                        contentDiv.innerHTML = `<pre class="whitespace-pre-wrap font-mono text-xs">${escapeHtml(rawContent)}</pre>`;
                    }
                } else {
                    const aiMessage = document.querySelector(`#message-${data.task_id} .ai-message`);
                    if (aiMessage) {
                        let mainContainer = aiMessage.querySelector(`#thinking-container-${data.task_id}`);
                        if (!mainContainer) {
                            mainContainer = document.createElement('div');
                            mainContainer.id = `thinking-container-${data.task_id}`;
                            mainContainer.className = 'space-y-3';
                            aiMessage.appendChild(mainContainer);
                        }
                        if (currentThinking && !mainContainer.contains(currentThinking)) {
                            mainContainer.appendChild(currentThinking);
                        }
                    }
                }
            }
            document.getElementById('chatContainer').scrollTop = document.getElementById('chatContainer').scrollHeight;
            return;
        }

        if (!streamContents[data.task_id]) {
            streamContents[data.task_id] = {};
            console.log(`[STREAM] Created new streamContents for task ${data.task_id}`);
        }
        if (!streamContents[data.task_id][data.step]) {
            streamContents[data.task_id][data.step] = '';
            console.log(`[STREAM] Created new step ${data.step} for task ${data.task_id}`);
        }
        const prevContent = streamContents[data.task_id][data.step];
        streamContents[data.task_id][data.step] += data.content;
        
        if (data.content === '[DONE]') {
            console.log(`[STREAM] Step ${data.step} completed for task ${data.task_id}`);
            console.log(`[STREAM] Content length: ${prevContent.length} -> ${streamContents[data.task_id][data.step].length}`);
            console.log(`[STREAM] Content preview: ${prevContent.substring(0, 100)}...`);
        }

        const stepNames = {
            'ai_decision': 'AI思考',
            'analyze_results': '结果分析'
        };

        let thinkingContainer = document.getElementById(`thinking-container-${data.task_id}-${data.step}`);
        if (!thinkingContainer && data.step === 'analyze_results') {
            thinkingContainer = document.getElementById(`thinking-report-${data.task_id}`);
        }
        if (!thinkingContainer) {
            thinkingContainer = document.getElementById(`thinking-container-${data.task_id}-生成报告`);
        }
        if (!thinkingContainer) {
            thinkingContainer = document.getElementById(`thinking-current-${data.task_id}`);
        }
        if (!thinkingContainer && !processedStreamSteps.has(stepKey)) {
            processedStreamSteps.add(stepKey);
            const prevContainer = document.getElementById(`thinking-current-${data.task_id}`);
            if (prevContainer) {
                prevContainer.id = `thinking-container-${data.task_id}-${data.step}`;
                prevContainer.dataset.step = data.step;
                const prevHeader = prevContainer.querySelector('.thinking-header');
                if (prevHeader) {
                    prevHeader.classList.add('thinking');
                    const displayStepName = data.step === 'analyze_results' ? '正在生成报告' : (stepNames[data.step] || data.step);
                    const displayIcon = data.step === 'analyze_results' ? '📊' : '💭';
                    prevHeader.innerHTML = `
                        <span class="flex items-center space-x-2">
                            <svg class="w-4 h-4 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11 -4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"></path>
                            </svg>
                            <span class="font-semibold text-blue-700">${displayIcon} ${displayStepName}</span>
                        </span>
                    `;
                }
                const prevContent = prevContainer.querySelector('.thinking-content');
                if (prevContent) {
                    prevContent.classList.add('streaming');
                }
            }
            thinkingContainer = prevContainer;
        } else if (thinkingContainer && !processedStreamSteps.has(stepKey)) {
            processedStreamSteps.add(stepKey);
            thinkingContainer.dataset.step = data.step;
            const header = thinkingContainer.querySelector('.thinking-header');
            if (header) {
                header.classList.add('thinking');
                const displayStepName = data.step === 'analyze_results' ? '正在生成报告' : (stepNames[data.step] || data.step);
                const displayIcon = data.step === 'analyze_results' ? '📊' : '💭';
                header.innerHTML = `
                    <span class="flex items-center space-x-2">
                        <svg class="w-4 h-4 animate-pulse" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11 -4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"></path>
                        </svg>
                        <span class="font-semibold text-blue-700">${displayIcon} ${displayStepName}</span>
                    </span>
                `;
            }
            const content = thinkingContainer.querySelector('.thinking-content');
            if (content) {
                content.classList.add('streaming');
            }
        }

        if (thinkingContainer) {
            const contentDiv = thinkingContainer.querySelector('.thinking-content');
            if (contentDiv) {
                contentDiv.innerHTML = escapeHtml(streamContents[data.task_id][data.step]);
            }
        }
    } else if (data.type === 'waiting_decision') {
        console.log('Waiting for human decision:', data);
        showWaitingDecisionPanel(data.task_id, data.findings, data.message);
    }
}

const pollingTasks = new Set();

async function fetchTaskDetails(taskId) {
    if (pollingTasks.has(taskId)) {
        return;
    }
    pollingTasks.add(taskId);

    try {
        const response = await fetch(`/api/v1/tasks/${taskId}`);
        const data = await response.json();
        if (data.success) {
            tasks[taskId] = data.task;
            updateTaskMessage(data.task);

            if (data.task.status === 'running') {
                setTimeout(() => {
                    pollingTasks.delete(taskId);
                    fetchTaskDetails(taskId);
                }, 2000);
            } else {
                pollingTasks.delete(taskId);
            }
        } else {
            pollingTasks.delete(taskId);
        }
    } catch (error) {
        console.error('Failed to fetch task details:', error);
        pollingTasks.delete(taskId);
    }
}

function addUserMessage(content) {
    const chatContainer = document.getElementById('chatContainer');
    const messageDiv = document.createElement('div');
    messageDiv.className = 'flex items-start space-x-3 justify-end';
    messageDiv.innerHTML = `
        <div class="user-message text-white rounded-2xl rounded-tr-sm px-5 py-4 max-w-[80%]">
            <p>${escapeHtml(content)}</p>
        </div>
        <div class="w-10 h-10 rounded-full bg-gray-300 flex items-center justify-center flex-shrink-0">
            <svg class="w-6 h-6 text-gray-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"></path>
            </svg>
        </div>
    `;
    chatContainer.appendChild(messageDiv);
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function addAssistantMessage(content) {
    const chatContainer = document.getElementById('chatContainer');
    const messageDiv = document.createElement('div');
    messageDiv.className = 'flex items-start space-x-3';
    messageDiv.innerHTML = `
        <div class="w-10 h-10 rounded-full bg-blue-500 flex items-center justify-center flex-shrink-0">
            <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path>
            </svg>
        </div>
        <div class="ai-message rounded-2xl rounded-tl-sm px-5 py-4 max-w-[80%]">
            <p>${escapeHtml(content)}</p>
        </div>
    `;
    chatContainer.appendChild(messageDiv);
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function addAIMessage(taskId) {
    const chatContainer = document.getElementById('chatContainer');

    const existingDiv = document.getElementById(`message-${taskId}`);
    if (existingDiv) {
        return;
    }

    processedStreamSteps.forEach(key => {
        if (key.startsWith(taskId + '-')) {
            processedStreamSteps.delete(key);
        }
    });
    createdReportContainers.delete(`report-${taskId}`);

    const messageDiv = document.createElement('div');
    messageDiv.id = `message-${taskId}`;
    messageDiv.className = 'flex items-start space-x-3';
    messageDiv.innerHTML = `
        <div class="w-10 h-10 rounded-full bg-blue-500 flex items-center justify-center flex-shrink-0">
            <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path>
            </svg>
        </div>
        <div class="ai-message rounded-2xl rounded-tl-sm px-5 py-4 max-w-[85%] flex-1">
            <div id="task-${taskId}-controls" class="flex items-center justify-between mb-3">
                <span class="text-sm font-semibold text-blue-700">🔄 任务执行中</span>
                <button onclick="cancelTask('${taskId}')" class="px-3 py-1 bg-red-500 hover:bg-red-600 text-white text-xs rounded-full transition">
                    终止任务
                </button>
            </div>
            <div id="thinking-container-${taskId}" class="space-y-3">
                <div id="thinking-current-${taskId}" class="thinking-item bg-white rounded-lg border border-gray-200 overflow-hidden">
                    <div class="thinking-header thinking flex items-center justify-between bg-blue-50 px-3 py-2 cursor-pointer"
                         onclick="toggleThinking(this.parentElement)">
                        <span class="flex items-center space-x-2">
                            <svg class="w-4 h-4 animate-spin" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
                            </svg>
                            <span class="font-semibold text-blue-700">💭 思考中...</span>
                        </span>
                        <svg class="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                        </svg>
                    </div>
                    <div class="thinking-content streaming px-3 py-2 text-sm whitespace-pre-wrap max-h-60 overflow-y-auto">
                        正在分析...
                    </div>
                </div>
            </div>
        </div>
    `;

    const placeholder = document.getElementById(`task-placeholder-${taskId}`);
    if (placeholder) {
        placeholder.replaceWith(messageDiv);
    } else {
        chatContainer.appendChild(messageDiv);
    }
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function toggleThinking(container) {
    const content = container.querySelector('.thinking-content');
    const arrow = container.querySelector('.thinking-header svg');
    if (content) {
        content.classList.toggle('hidden');
    }
    if (arrow) {
        arrow.classList.toggle('rotate-180');
    }
}

function collapseThinking(container) {
    const content = container.querySelector('.thinking-content');
    const arrow = container.querySelector('.thinking-header svg');
    if (content) {
        content.classList.add('hidden');
    }
    if (arrow) {
        arrow.classList.remove('rotate-180');
    }
}

function updateTaskMessage(task) {
    const messageDiv = document.getElementById(`message-${task.id}`);
    if (!messageDiv) return;

    const content = messageDiv.querySelector('.ai-message');
    const progress = task.progress || {};
    const stepNames = {
        'ai_decision': 'AI决策中',
        'analyze_results': '结果分析'
    };

    let html = '';

    if (task.status === 'running') {
        const controlsDiv = document.getElementById(`task-${task.id}-controls`);
        if (controlsDiv) {
            controlsDiv.innerHTML = `
                <span class="text-sm font-semibold text-blue-700">🔍 ${progress.step_name || '处理中...'}</span>
                <div class="flex items-center space-x-2">
                    <span class="text-xs text-gray-500">迭代 ${progress.iteration || 1}</span>
                    <div class="w-20 bg-gray-200 rounded-full h-2">
                        <div class="progress-bar bg-blue-500 h-2 rounded-full" style="width: ${progress.percentage || 0}%"></div>
                    </div>
                    <button onclick="cancelTask('${task.id}')" class="px-3 py-1 bg-red-500 hover:bg-red-600 text-white text-xs rounded-full transition">
                        终止
                    </button>
                </div>
            `;
        }

        document.getElementById('chatContainer').scrollTop = document.getElementById('chatContainer').scrollHeight;
        return;
    } else if (task.status === 'completed') {
        const hasStream = streamContents[task.id] && Object.keys(streamContents[task.id]).length > 0;
        const hasAnalysis = task.llm_analysis_raw;

        const existingMessageDiv = document.getElementById(`message-${task.id}`);
        if (existingMessageDiv) {
            const controlsDiv = document.getElementById(`task-${task.id}-controls`);
            if (controlsDiv) {
                controlsDiv.innerHTML = `
                    <span class="text-sm font-semibold text-green-700">✅ 测试完成</span>
                    <button onclick="downloadReport('${task.id}')" class="px-3 py-1 bg-green-500 hover:bg-green-600 text-white text-xs rounded-full transition flex items-center space-x-1">
                        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                        </svg>
                        <span>下载报告</span>
                    </button>
                `;
            }

            const thinkingContainer = document.getElementById(`thinking-container-${task.id}`);
            const aiDecisionContainer = document.getElementById(`thinking-container-${task.id}-ai_decision`);
            const reportContainer = document.getElementById(`thinking-report-${task.id}`);
            if (thinkingContainer || aiDecisionContainer || reportContainer) {
                return;
            }
            if (!thinkingContainer && (hasStream || hasAnalysis)) {
                const aiMessage = existingMessageDiv.querySelector('.ai-message');
                if (aiMessage) {
                    let streamHtml = `<div id="thinking-container-${task.id}" class="space-y-3">`;

                    if (hasStream) {
                        streamHtml += Object.entries(streamContents[task.id]).map(([step, content], index) => {
                            const stepNames = {
                                'ai_decision': 'AI思考',
                                'analyze_results': '结果分析'
                            };
                            const stepName = stepNames[step] || step;
                            const displayIcon = step === 'analyze_results' ? '📊' : '💭';
                            const displayStepName = step === 'analyze_results' ? '报告生成' : stepName;
                            return `
                                <div class="thinking-item bg-white rounded-lg border border-gray-200 overflow-hidden">
                                    <div class="thinking-header done flex items-center justify-between bg-green-50 px-3 py-2 cursor-pointer"
                                         onclick="toggleThinking(this.parentElement)">
                                        <span class="flex items-center space-x-2">
                                            <svg class="w-4 h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                                            </svg>
                                            <span class="font-semibold thinking-text text-green-700">${displayIcon} ${displayStepName} 完成</span>
                                        </span>
                                        <svg class="w-4 h-4 text-gray-500 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                                        </svg>
                                    </div>
                                    <div class="thinking-content px-3 py-2 text-sm whitespace-pre-wrap max-h-60 overflow-y-auto" style="font-style: italic; color: #6b7280; background-color: #f9fafb; border-left: 3px solid #93c5fd; padding-left: 12px;">
                                        ${escapeHtml(content)}
                                    </div>
                                </div>
                            `;
                        }).join('');
                    } else if (hasAnalysis) {
                        streamHtml += `
                            <div class="thinking-item bg-white rounded-lg border border-gray-200 overflow-hidden">
                                <div class="thinking-header done flex items-center justify-between bg-green-50 px-3 py-2 cursor-pointer"
                                     onclick="toggleThinking(this.parentElement)">
                                    <span class="flex items-center space-x-2">
                                        <svg class="w-4 h-4 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
                                        </svg>
                                        <span class="font-semibold thinking-text text-green-700">📊 报告生成 完成</span>
                                    </span>
                                    <svg class="w-4 h-4 text-gray-500 transition-transform" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path>
                                    </svg>
                                </div>
                                <div class="thinking-content px-3 py-2 text-sm whitespace-pre-wrap max-h-60 overflow-y-auto" style="font-style: italic; color: #6b7280; background-color: #f9fafb; border-left: 3px solid #93c5fd; padding-left: 12px;">
                                    ${escapeHtml(task.llm_analysis_raw)}
                                </div>
                            </div>
                        `;
                    }

                    streamHtml += `</div>`;
                    aiMessage.insertAdjacentHTML('beforeend', streamHtml);
                }
            }
        }

        document.getElementById('chatContainer').scrollTop = document.getElementById('chatContainer').scrollHeight;
        return;
    } else if (task.status === 'failed') {
        const messageDiv = document.getElementById(`message-${task.id}`);
        if (messageDiv) {
            const content = messageDiv.querySelector('.ai-message');
            if (content) {
                content.innerHTML = `
                    <div class="space-y-2">
                        <p class="font-semibold text-red-700">❌ 测试失败</p>
                        <p class="text-sm text-gray-600">${task.errors?.[0] || '未知错误'}</p>
                    </div>
                `;
            }
        }
        document.getElementById('chatContainer').scrollTop = document.getElementById('chatContainer').scrollHeight;
        return;
    } else if (task.status === 'cancelled') {
        const messageDiv = document.getElementById(`message-${task.id}`);
        if (messageDiv) {
            const content = messageDiv.querySelector('.ai-message');
            if (content) {
                content.innerHTML = `
                    <div class="space-y-2">
                        <p class="font-semibold text-yellow-700">⏹️ 任务已终止</p>
                        <p class="text-sm text-gray-600">用户已终止该任务</p>
                    </div>
                `;
            }
        }
        document.getElementById('chatContainer').scrollTop = document.getElementById('chatContainer').scrollHeight;
        return;
    }

    if (html) {
        content.innerHTML = html;
    }
    document.getElementById('chatContainer').scrollTop = document.getElementById('chatContainer').scrollHeight;
}

function downloadReport(taskId) {
    fetch(`/api/v1/tasks/${taskId}/report`)
        .then(response => {
            if (!response.ok) {
                throw new Error('报告生成失败');
            }
            return response.json();
        })
        .then(data => {
            if (data.success) {
                const blob = new Blob([data.report_html], { type: 'text/html' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `渗透测试报告_${taskId.substring(0, 8)}.html`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                showToast('报告下载成功！');
            } else {
                showToast('报告生成失败', 4000);
            }
        })
        .catch(error => {
            console.error('下载报告失败:', error);
            showToast('下载报告失败: ' + error.message, 4000);
        });
}

function showToast(message, duration = 3000) {
    const toast = document.createElement('div');
    toast.className = 'toast-notification fixed top-4 left-1/2 transform -translate-x-1/2 bg-gray-800 text-white px-6 py-3 rounded-lg shadow-lg z-50 flex items-center space-x-2';
    toast.innerHTML = `
        <svg class="w-5 h-5 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path>
        </svg>
        <span>${message}</span>
    `;
    document.body.appendChild(toast);
    setTimeout(() => {
        if (toast.parentElement) {
            toast.remove();
        }
    }, duration);
}

function showWaitingDecisionPanel(taskId, findings, message) {
    const chatContainer = document.getElementById('chatContainer');

    let findingsHtml = '';
    if (findings && findings.length > 0) {
        findingsHtml = findings.map(f => `<li class="text-sm text-gray-700">• ${escapeHtml(f)}</li>`).join('');
    } else {
        findingsHtml = '<li class="text-sm text-gray-500">暂无发现</li>';
    }

    const panelHtml = `
        <div id="waiting-decision-panel-${taskId}" class="fixed inset-0 bg-black bg-opacity-50 z-50 flex items-center justify-center">
            <div class="bg-white rounded-2xl shadow-2xl max-w-lg w-full mx-4 overflow-hidden">
                <div class="bg-blue-500 px-6 py-4">
                    <h3 class="text-xl font-bold text-white">🔍 侦查完成，等待人工决策</h3>
                    <p class="text-blue-100 text-sm mt-1">请查看以下发现，决定下一步攻击方向</p>
                </div>
                <div class="p-6 max-h-96 overflow-y-auto">
                    <h4 class="font-semibold text-gray-700 mb-3">📋 发现摘要：</h4>
                    <ul class="space-y-2 mb-4">
                        ${findingsHtml}
                    </ul>
                    <div class="border-t border-gray-200 pt-4">
                        <label class="block text-sm font-medium text-gray-700 mb-2">🎯 输入攻击命令（可选）：</label>
                        <textarea id="human-commands-${taskId}"
                            class="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                            rows="4"
                            placeholder="例如：sqlmap -u http://target/login --level=2&#10;或留空让AI自动决定"></textarea>
                        <p class="text-xs text-gray-500 mt-1">多个命令用换行分隔</p>
                    </div>
                </div>
                <div class="px-6 py-4 bg-gray-50 flex justify-end space-x-3">
                    <button onclick="submitHumanDecision('${taskId}', 'done')"
                        class="px-4 py-2 bg-gray-300 hover:bg-gray-400 text-gray-700 rounded-lg text-sm font-medium transition">
                        结束测试
                    </button>
                    <button onclick="submitHumanDecision('${taskId}', 'continue')"
                        class="px-4 py-2 bg-blue-500 hover:bg-blue-600 text-white rounded-lg text-sm font-medium transition">
                        让AI继续
                    </button>
                </div>
            </div>
        </div>
    `;

    document.body.insertAdjacentHTML('beforeend', panelHtml);
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

async function submitHumanDecision(taskId, action) {
    const panel = document.getElementById(`waiting-decision-panel-${taskId}`);
    const commandsText = document.getElementById(`human-commands-${taskId}`)?.value || '';
    const commands = commandsText.split('\n').map(c => c.trim()).filter(c => c.length > 0);

    if (panel) {
        panel.remove();
    }

    try {
        const response = await fetch(`/api/v1/tasks/${taskId}/human-decision`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                commands: commands,
                action: action,
                reason: commands.length > 0 ? '用户指定攻击命令' : '用户确认继续'
            })
        });

        const data = await response.json();
        if (data.success) {
            showToast(action === 'done' ? '测试已结束' : 'AI正在继续...');
            console.log('Human decision submitted, waiting for AI response...');
        } else {
            console.error('Human decision failed:', data);
            showToast('提交决策失败: ' + (data.error || '未知错误'), 4000);
        }
    } catch (error) {
        console.error('Failed to submit human decision:', error);
        showToast('提交决策失败', 4000);
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function parseFirstJson(text) {
    const firstOpen = text.indexOf('{');
    if (firstOpen === -1) return null;

    let depth = 0;
    for (let i = firstOpen; i < text.length; i++) {
        if (text[i] === '{') depth++;
        else if (text[i] === '}') {
            depth--;
            if (depth === 0) {
                const jsonStr = text.substring(firstOpen, i + 1);
                try {
                    return JSON.parse(jsonStr);
                } catch (e) {
                    return null;
                }
            }
        }
    }
    return null;
}

function formatJson(json) {
    const formatted = JSON.stringify(json, null, 2);
    return escapeHtml(formatted)
        .replace(/\\n/g, '<br>')
        .replace(/  /g, '&nbsp;&nbsp;');
}

async function loadLLMProviders() {
    try {
        const response = await fetch('/api/v1/llm/providers');
        const data = await response.json();
        const providerSelect = document.getElementById('llmProvider');

        const providerNames = {
            'openai': 'OpenAI',
            'qwen': '通义千问',
            'deepseek': 'DeepSeek',
            'doubao': '豆包'
        };

        if (data.providers && data.providers.length > 0) {
            providerSelect.innerHTML = data.providers.map(provider =>
                `<option value="${provider}" ${provider === data.current_provider ? 'selected' : ''}>
                    ${providerNames[provider] || provider}
                </option>`
            ).join('');
        }
    } catch (error) {
        console.error('Failed to load LLM providers:', error);
    }
}

async function switchLLMProvider() {
    try {
        const providerSelect = document.getElementById('llmProvider');
        const provider = providerSelect.value;

        const response = await fetch('/api/v1/llm/switch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider: provider })
        });

        const data = await response.json();
        if (data.success) {
            addSystemMessage(`已切换到 ${provider} 模型`);
        } else {
            alert('切换模型失败: ' + (data.error || '未知错误'));
        }
    } catch (error) {
        console.error('Failed to switch LLM provider:', error);
        alert('切换模型失败');
    }
}

function addSystemMessage(content) {
    const chatContainer = document.getElementById('chatContainer');
    const messageDiv = document.createElement('div');
    messageDiv.className = 'flex justify-center';
    messageDiv.innerHTML = `
        <div class="bg-gray-100 text-gray-600 px-4 py-2 rounded-full text-sm">
            ${content}
        </div>
    `;
    chatContainer.appendChild(messageDiv);
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

async function cancelTask(taskId) {
    if (!confirm('确定要终止这个任务吗？')) {
        return;
    }

    try {
        const response = await fetch(`/api/v1/tasks/${taskId}/cancel`, {
            method: 'POST'
        });

        const data = await response.json();
        if (data.success) {
            runningTaskId = null;

            const currentConv = conversations[currentConversationId];
            if (currentConv && currentConv.task_id === taskId) {
                currentConv.task_id = null;
            }
        }
    } catch (error) {
        console.error('Failed to cancel task:', error);
        alert('终止任务失败');
    }
}

document.getElementById('chatForm').addEventListener('submit', async (e) => {
    e.preventDefault();

    if (isSubmitting) {
        console.log('[DEBUG] Already submitting, ignoring duplicate submit');
        return;
    }
    isSubmitting = true;

    const input = document.getElementById('messageInput');
    const target = input.value.trim();

    if (!target) {
        isSubmitting = false;
        return;
    }

    const submitBtn = e.target.querySelector('button[type="submit"]');
    if (submitBtn && submitBtn.disabled) {
        isSubmitting = false;
        return;
    }
    if (submitBtn) submitBtn.disabled = true;

    if (!currentConversationId) {
        await createNewConversation();
    }

    const conv = conversations[currentConversationId];
    if (conv) {
        if (!conv.name || conv.name === '新对话') {
            conv.name = target.substring(0, 20) + (target.length > 20 ? '...' : '');
        }

        try {
            await fetch(`/api/v1/conversations/${currentConversationId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: conv.name
                })
            });
        } catch (e) {
            console.error('[DEBUG] Failed to update conversation:', e);
        }
    } else {
        console.error('[DEBUG] No conversation found for id:', currentConversationId);
    }

    addUserMessage(target);
    input.value = '';
    updateContextInfo();

    try {
        const maxIterations = parseInt(document.getElementById('maxIterations').value);
        const hybridMode = document.getElementById('hybridMode').value === 'true';
        const humanInteraction = document.getElementById('humanInteractionMode').value === 'true';

        const response = await fetch('/api/v1/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                user_message: target,
                options: {
                    max_iterations: maxIterations,
                    hybrid_mode: hybridMode,
                    human_interaction: humanInteraction
                },
                conversation_id: currentConversationId,
                context_settings: contextSettings
            })
        });

        const data = await response.json();
        if (data.success) {
            tasks[data.task.id] = data.task;
            activeTaskId = data.task.id;
            if (conv) {
                conv.task_id = data.task.id;
            }
            addAIMessage(data.task.id);
            renderConversationList();

            if (!executedTasks.has(data.task.id)) {
                executedTasks.add(data.task.id);
                await fetch(`/api/v1/tasks/${data.task.id}/execute`, {
                    method: 'POST'
                });
            } else {
                console.log(`[DEBUG] Task ${data.task.id} already executed, skipping`);
            }
        }
    } catch (error) {
        console.error('Failed to create task:', error);
        addSystemMessage('创建任务失败，请重试');
    } finally {
        if (submitBtn) submitBtn.disabled = false;
        isSubmitting = false;
    }
});

loadConversations();
loadLLMProviders();
initWebSocket();