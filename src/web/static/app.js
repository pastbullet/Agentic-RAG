// --- 全局状态 ---
const state = {
    pdfDoc: null,
    pageNum: 1,
    scale: 1.0,
    pageRendering: false,
    pageNumPending: null,
    canvas: document.getElementById('the-canvas'),
    ctx: document.getElementById('the-canvas').getContext('2d'),
    pdfBlobUrl: null,
    currentDocName: "",
    isPdfOpen: false
};

// --- UI 元素 ---
const els = {
    app: document.getElementById('appContainer'),
    sessionsList: document.getElementById('sessionsList'),
    messagesContainer: document.getElementById('messagesContainer'),
    queryInput: document.getElementById('qaQueryInput'),
    sendBtn: document.getElementById('qaBtn'),
    currentDocLabel: document.getElementById('currentDocLabel'),
    
    // PDF controls
    pdfFileName: document.getElementById('pdfFileName'),
    pageInfo: document.getElementById('pageInfo'),
    
    // Settings
    processPathInput: document.getElementById('processPathInput'),
    processPathBtn: document.getElementById('processPathBtn'),
    uploadInput: document.getElementById('uploadInput'),
    settingsPanel: document.getElementById('settingsPanel'),
    toggleSettingsBtn: document.getElementById('toggleSettingsBtn'),
    forceCheckbox: document.getElementById('forceCheckbox')
};

// --- PDF 功能 ---

function togglePdfPanel(show) {
    state.isPdfOpen = show;
    if (show) {
        els.app.classList.add('pdf-active');
    } else {
        els.app.classList.remove('pdf-active');
    }
    // 重新渲染以适应 Canvas 宽度变化 (延时一下等待 transition)
    setTimeout(() => {
        if(state.pdfDoc) queueRenderPage(state.pageNum);
    }, 350);
}

document.getElementById('closePdfBtn').onclick = () => togglePdfPanel(false);

async function loadPdf(docName, page = 1) {
    if (!docName) return;
    
    // 展开面板
    togglePdfPanel(true);
    els.pdfFileName.textContent = docName;
    state.currentDocName = docName;
    els.currentDocLabel.textContent = docName; // 更新顶部状态

    // 简单缓存
    if (els.pdfFileName.dataset.loadedDoc !== docName) {
        try {
            const resp = await fetch(`/api/pdf/${encodeURIComponent(docName)}`, { headers: { Accept: "application/pdf" }});
            if (!resp.ok) throw new Error("PDF not found");
            const blob = await resp.blob();
            if (state.pdfBlobUrl) URL.revokeObjectURL(state.pdfBlobUrl);
            state.pdfBlobUrl = URL.createObjectURL(blob);
            els.pdfFileName.dataset.loadedDoc = docName;
            
            const loadingTask = pdfjsLib.getDocument(state.pdfBlobUrl);
            state.pdfDoc = await loadingTask.promise;
        } catch (e) {
            alert("无法加载 PDF: " + e.message);
            return;
        }
    }

    state.pageNum = Number(page) || 1;
    queueRenderPage(state.pageNum);
}

function renderPage(num) {
    state.pageRendering = true;
    state.pdfDoc.getPage(num).then(page => {
        // 根据容器宽度自适应缩放
        const containerWidth = document.getElementById('pdfContainer').clientWidth - 40;
        const viewportUnscaled = page.getViewport({scale: 1});
        // 只有当自动模式时才计算 scale，否则用手动 scale
        // 这里简化为：每次都适配宽度，除非用户手动缩放过(逻辑稍复杂，这里先简化为固定 scale 或宽适配)
        // 为简单起见，这里按宽度适配:
        const scale = containerWidth / viewportUnscaled.width;
        // 如果想支持 +/- 按钮，则使用 state.scale，这里先更新 state.scale 为适配宽度的值
        if(!state.scaleUpdated) { 
             state.scale = scale > 0 ? scale : 1; 
        }
        
        const viewport = page.getViewport({scale: state.scale});
        state.canvas.height = viewport.height;
        state.canvas.width = viewport.width;

        const renderCtx = { canvasContext: state.ctx, viewport: viewport };
        page.render(renderCtx).promise.then(() => {
            state.pageRendering = false;
            if (state.pageNumPending !== null) {
                renderPage(state.pageNumPending);
                state.pageNumPending = null;
            }
        });
    });
    els.pageInfo.textContent = `${num} / ${state.pdfDoc.numPages}`;
}

function queueRenderPage(num) {
    if (state.pageRendering) state.pageNumPending = num;
    else renderPage(num);
}

// PDF 按钮
document.getElementById('prevPageBtn').onclick = () => { if(state.pageNum > 1) queueRenderPage(--state.pageNum); };
document.getElementById('nextPageBtn').onclick = () => { if(state.pdfDoc && state.pageNum < state.pdfDoc.numPages) queueRenderPage(++state.pageNum); };
document.getElementById('zoomInBtn').onclick = () => { state.scale += 0.2; state.scaleUpdated = true; queueRenderPage(state.pageNum); };
document.getElementById('zoomOutBtn').onclick = () => { if(state.scale > 0.4) { state.scale -= 0.2; state.scaleUpdated = true; queueRenderPage(state.pageNum); }};

// --- 聊天 UI 逻辑 ---

function createMessageBubble(role, initialText = "") {
    // 移除 Welcome screen
    const welcome = document.querySelector('.welcome-screen');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = `message ${role}`;
    
    // Avatar
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = role === 'user' ? 'U' : 'AI';
    
    // Content
    const content = document.createElement('div');
    content.className = 'message-content';
    
    // 如果是 AI，可以包含 trace 区域和 markdown 区域
    let traceArea = null;
    let textArea = null;
    let citationArea = null;

    if (role === 'ai') {
        // Trace Foldable
        const details = document.createElement('details');
        details.className = 'trace-details';
        const summary = document.createElement('summary');
        summary.className = 'trace-summary';
        summary.textContent = 'Thinking process...';
        const pre = document.createElement('pre');
        pre.className = 'trace-log';
        details.appendChild(summary);
        details.appendChild(pre);
        details.style.display = 'none'; // 默认隐藏，有内容再显示
        
        traceArea = pre;
        div.traceContainer = details; // 挂载引用方便更新
        content.appendChild(details);
        
        // Text
        textArea = document.createElement('div');
        textArea.className = 'text-body';
        textArea.innerHTML = initialText;
        content.appendChild(textArea);
        
        // Citation
        citationArea = document.createElement('div');
        citationArea.className = 'citations-area';
        citationArea.style.display = 'none';
        content.appendChild(citationArea);
    } else {
        content.textContent = initialText;
    }

    div.appendChild(avatar);
    div.appendChild(content);
    els.messagesContainer.appendChild(div);
    scrollToBottom();
    
    return {
        div,
        traceArea,
        textArea,
        citationArea,
        traceContainer: div.traceContainer
    };
}

function scrollToBottom() {
    els.messagesContainer.scrollTop = els.messagesContainer.scrollHeight;
}

function appendTrace(bubble, text) {
    if (!bubble || !bubble.traceArea) return;
    bubble.traceContainer.style.display = 'block';
    bubble.traceArea.textContent += text + "\n";
}

function updateAiMessage(bubble, text) {
    if (!bubble || !bubble.textArea) return;
    // 简单的换行处理，实际可用 marked.js
    bubble.textArea.innerHTML = text.replace(/\n/g, "<br>");
    scrollToBottom();
}

function renderCitations(bubble, citations) {
    if (!bubble || !bubble.citationArea || !citations.length) return;
    bubble.citationArea.innerHTML = '';
    bubble.citationArea.style.display = 'flex';
    
    citations.forEach(c => {
        const btn = document.createElement('button');
        btn.className = 'citation-chip';
        btn.textContent = `📄 ${c.doc_name} p.${c.page}`;
        btn.onclick = () => loadPdf(c.doc_name, c.page);
        bubble.citationArea.appendChild(btn);
    });
}

// --- 业务逻辑 (Stream & API) ---

async function sendQuery() {
    const query = els.queryInput.value.trim();
    if (!query) return;
    
    // 如果没有选中文档，尝试自动探测或报错 (简单起见，假设用户已处理过或输入了)
    // 实际应检查 state.currentDocName
    
    els.queryInput.value = '';
    createMessageBubble('user', query);
    
    const aiBubble = createMessageBubble('ai', 'Thinking...');
    let fullAnswer = "";
    
    try {
        const resp = await fetch("/api/qa/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                query,
                doc_name: state.currentDocName || null, 
                // 如果没有 doc_name，用户可能想通过 query 自动匹配，或者需要 UI 提示
                force: els.forceCheckbox.checked
            }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            
            const lines = buffer.split('\n\n');
            buffer = lines.pop(); // 保留未完成的块

            for (const block of lines) {
                if (!block.startsWith('data: ')) continue;
                try {
                    const event = JSON.parse(block.slice(6));
                    handleStreamEvent(event, aiBubble);
                    if (event.type === 'final_answer') {
                        fullAnswer = event.answer_clean;
                    }
                } catch (e) { console.error(e); }
            }
        }
    } catch (e) {
        updateAiMessage(aiBubble, "Error: " + e.message);
    }
}

function handleStreamEvent(event, bubble) {
    if (event.type === 'tool_call') {
        appendTrace(bubble, `→ Call Tool: ${event.tool}`);
    } else if (event.type === 'stage_done') {
        // 如果后端返回了 doc_name，更新当前状态
        if (event.doc_name) {
            state.currentDocName = event.doc_name;
            els.currentDocLabel.textContent = event.doc_name;
        }
    } else if (event.type === 'final_answer') {
        updateAiMessage(bubble, event.answer_clean || event.answer);
        renderCitations(bubble, event.citations);
    } 
    // 其他事件可根据需要记录到 trace
}

// --- 文件上传与处理 ---

els.processPathBtn.onclick = async () => {
    const path = els.processPathInput.value.trim();
    if(!path) return;
    createMessageBubble('ai', `Processing path: ${path}...`);
    // 调用 API (复用之前的逻辑，略简化)
    const resp = await fetch("/api/process/path", {
        method: "POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({ pdf_path: path, force: els.forceCheckbox.checked })
    });
    const data = await resp.json();
    if(data.ok) {
        state.currentDocName = data.result.doc_name;
        createMessageBubble('ai', `Ready! Document loaded: ${data.result.doc_name}`);
        loadSessions(); // 刷新列表
    }
};

els.uploadInput.onchange = async (e) => {
    const file = e.target.files[0];
    if(!file) return;
    createMessageBubble('ai', `Uploading ${file.name}...`);
    const form = new FormData();
    form.set("file", file);
    const resp = await fetch("/api/process/upload", { method: "POST", body: form });
    const data = await resp.json();
    if(data.ok) {
        state.currentDocName = data.result.doc_name;
        createMessageBubble('ai', `Processed ${data.result.doc_name}. You can ask questions now.`);
        loadSessions();
    }
};

// --- 会话列表 ---
async function loadSessions() {
    const resp = await fetch("/api/sessions");
    const data = await resp.json();
    els.sessionsList.innerHTML = data.sessions.map(s => `
        <div class="session-item" onclick="restoreSession('${s.id}')">
            <div style="font-weight:500">${s.doc_name || "Unknown"}</div>
            <div style="font-size:12px;color:#888">${s.query.slice(0,30)}...</div>
        </div>
    `).join('');
}

window.restoreSession = async (id) => {
    const resp = await fetch(`/api/sessions/${id}`);
    const data = await resp.json();
    // 清空当前聊天
    els.messagesContainer.innerHTML = '';
    state.currentDocName = data.doc_name;
    els.currentDocLabel.textContent = data.doc_name;
    
    // 恢复两条消息
    createMessageBubble('user', data.query);
    const aiMsg = createMessageBubble('ai', data.answer_clean);
    renderCitations(aiMsg, data.citations);
    // 自动打开第一页引用
    if(data.doc_name && data.pages_retrieved.length) {
        loadPdf(data.doc_name, data.pages_retrieved[0]);
    }
};

// --- 初始化绑定 ---
els.sendBtn.onclick = sendQuery;
els.queryInput.onkeydown = (e) => {
    if(e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendQuery();
    }
};
els.toggleSettingsBtn.onclick = () => els.settingsPanel.classList.toggle('hidden');
document.getElementById('newChatBtn').onclick = () => {
    location.reload(); // 简单重置
};

loadSessions();