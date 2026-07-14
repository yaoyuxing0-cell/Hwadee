// ==================== 0. API 配置与工具函数 ====================
const API_BASE = 'http://192.168.62.98:8080/api/v1';
const API_TIMEOUT_MS = 5000;

/**
 * 更新后端连接状态指示器
 */
function setApiStatus(connected) {
    const dot = document.getElementById('api-status-dot');
    const text = document.getElementById('api-status-text');
    if (dot && text) {
        if (connected) {
            dot.style.background = '#52c41a';
            text.innerText = '后端服务已连接';
            text.style.color = '#52c41a';
        } else {
            dot.style.background = '#faad14';
            text.innerText = '后端未连接，使用本地数据';
            text.style.color = '#faad14';
        }
    }
}

/**
 * 通用 GET 请求，失败返回 null
 */
async function apiGet(path) {
    try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
        const resp = await fetch(API_BASE + path, { signal: controller.signal, headers: { 'Accept-Charset': 'utf-8' } });
        clearTimeout(timer);
        if (!resp.ok) return null;
        const json = await resp.json();
        if (json.code === 200) {
            setApiStatus(true);
            return json.data;
        }
        return null;
    } catch (e) {
        console.warn('API GET failed, fallback to local:', e.message);
        setApiStatus(false);
        return null;
    }
}

/**
 * 通用 POST 请求
 * 返回 { data: ... } 成功，{ error: '...' } 业务错误，null 网络不通
 */
async function apiPost(path, body) {
    try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
        const resp = await fetch(API_BASE + path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json; charset=utf-8', 'Accept-Charset': 'utf-8' },
            body: JSON.stringify(body),
            signal: controller.signal
        });
        clearTimeout(timer);
        if (!resp.ok) {
            const json = await resp.json().catch(() => ({}));
            return { error: json.message || '请求失败 (' + resp.status + ')' };
        }
        const json = await resp.json();
        if (json.code === 200) return { data: json.data };
        return { error: json.message || '未知错误' };
    } catch (e) {
        console.warn('API POST failed, fallback to local:', e.message);
        return null;
    }
}


// ==================== 1. 初始化 ECharts 实例 ====================
const chartDom = document.getElementById('graph-container');
const myChart = echarts.init(chartDom);

const relationMap = {
    'HAS_SYMPTOM': '具有症状', 'TREATED_BY': '采用治疗方案', 'TREATED_WITH_DRUG': '使用药品',
    'REQUIRES_EXAM': '需要检查', 'HAS_COMPLICATION': '具有并发症', 'BELONGS_TO_DEPARTMENT': '属于科室',
    'CONTRAINDICATED_FOR': '禁用于某类人群', 'INTERACTS_WITH': '与某药物相互作用'
};

const CATEGORY_NAMES = [
    'Disease (疾病)', 'Symptom (症状)', 'Drug (药品)', 'Examination (检查)',
    'Treatment (治疗)', 'Department (科室)', 'Complication (并发症)', 'Population (特殊人群)'
];

// 后端新格式 nodeType 字符串 → ECharts category 整数
const NODE_TYPE_TO_CATEGORY = {
    'Disease': 0, 'Symptom': 1, 'Drug': 2,
    'Examination': 3, 'Treatment': 4,
    'Department': 5, 'Complication': 6, 'Population': 7
};

/** 当前图谱数据（旧格式），用于增量扩展 */
let currentGraphData = null;
/** 上一个被点击的节点名，用于点击链上裁剪旧节点 */
let lastClickedNodeName = null;

/**
 * 将后端新格式转为 ECharts 旧格式
 * 新：nodes[{id,caption,nodeType,color,size,description}] + relationships[{from,to,type,caption,color}]
 * 旧：nodes[{name,category,symbolSize}] + links[{source,target,label}]
 */
function transformGraphData(apiData) {
    if (!apiData || !apiData.nodes || apiData.nodes.length === 0) return null;
    const first = apiData.nodes[0];
    // 已是旧格式（有 name 无 nodeType）→ 原样返回
    if (first.name !== undefined && first.nodeType === undefined) return apiData;

    const nodes = apiData.nodes.map(n => ({
        name: n.id,
        category: NODE_TYPE_TO_CATEGORY[n.nodeType] != null ? NODE_TYPE_TO_CATEGORY[n.nodeType] : 0,
        symbolSize: n.size || 30,
        description: n.description || '',
        nodeType: n.nodeType || '',
        itemStyle: n.color ? { color: n.color } : undefined
    }));

    const links = (apiData.relationships || []).map(r => ({
        source: r.from,
        target: r.to,
        label: { show: true, formatter: r.caption || r.type || '' },
        lineStyle: r.color ? { color: r.color } : undefined
    }));

    return { nodes, links, categories: [] };
}

/**
 * 从节点列表中按类型多样性选取最多 maxCount 个节点
 * 优先选符号大的，同时保证不同类型都有代表（轮询选取）
 */
function selectDiverseNodes(nodes, maxCount) {
    if (nodes.length <= maxCount) return nodes;

    // 按 category 分组，每组内按 symbolSize 降序排列
    const groups = {};
    nodes.forEach(n => {
        const cat = n.category != null ? n.category : 0;
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(n);
    });
    Object.values(groups).forEach(g => g.sort((a, b) => (b.symbolSize || 30) - (a.symbolSize || 30)));

    // 轮询从每组取一个，直到满 maxCount
    const result = [];
    const indices = {};  // groupKey → 已取到的位置
    Object.keys(groups).forEach(k => { indices[k] = 0; });

    while (result.length < maxCount) {
        let added = false;
        for (const cat of Object.keys(groups)) {
            if (indices[cat] < groups[cat].length) {
                result.push(groups[cat][indices[cat]]);
                indices[cat]++;
                added = true;
                if (result.length >= maxCount) break;
            }
        }
        if (!added) break;  // 所有组都取完了
    }
    return result;
}

// 图谱核心基础数据集（后端不可用时的本地降级数据）
const localGraphData = {
    nodes: [
        { name: '高血压', category: 0, symbolSize: 65 }, { name: '头晕', category: 1, symbolSize: 45 },
        { name: '心悸', category: 1, symbolSize: 45 }, { name: '硝苯地平', category: 2, symbolSize: 48 },
        { name: '卡托普利', category: 2, symbolSize: 48 }, { name: '心电图', category: 3, symbolSize: 45 },
        { name: '低盐饮食', category: 4, symbolSize: 45 }, { name: '心血管内科', category: 5, symbolSize: 50 },
        { name: '脑卒中', category: 6, symbolSize: 45 }, { name: '孕妇', category: 7, symbolSize: 45 }
    ],
    links: [
        { source: '高血压', target: '头晕', label: { show: true, formatter: relationMap['HAS_SYMPTOM'] } },
        { source: '高血压', target: '心悸', label: { show: true, formatter: relationMap['HAS_SYMPTOM'] } },
        { source: '高血压', target: '硝苯地平', label: { show: true, formatter: relationMap['TREATED_WITH_DRUG'] } },
        { source: '高血压', target: '卡托普利', label: { show: true, formatter: relationMap['TREATED_WITH_DRUG'] } },
        { source: '高血压', target: '心电图', label: { show: true, formatter: relationMap['REQUIRES_EXAM'] } },
        { source: '高血压', target: '低盐饮食', label: { show: true, formatter: relationMap['TREATED_BY'] } },
        { source: '高血压', target: '心血管内科', label: { show: true, formatter: relationMap['BELONGS_TO_DEPARTMENT'] } },
        { source: '高血压', target: '脑卒中', label: { show: true, formatter: relationMap['HAS_COMPLICATION'] } }
    ],
    categories: [
        { name: 'Disease (疾病)' }, { name: 'Symptom (症状)' }, { name: 'Drug (药品)' }, { name: 'Examination (检查)' },
        { name: 'Treatment (治疗)' }, { name: 'Department (科室)' }, { name: 'Complication (并发症)' }, { name: 'Population (特殊人群)' }
    ]
};

const localBackupDetails = {
    '高血压': { name: '高血压', category: 'Disease (疾病)', definition: '以体循环动脉血压增高为主要特征的临床综合征。', indications: '需结合临床指南定期监测。', badReactions: '早期多无症状。' },
    '硝苯地平': { name: '硝苯地平', category: 'Drug (药品)', definition: '钙通道阻滞剂（CCB），用于降血压。', indications: '高血压、心绞痛。', badReactions: '下肢水肿。' }
};


// ==================== 2. 图谱渲染引擎 ====================

/**
 * 根据节点数据构建 ECharts categories 数组
 */
function buildCategories(nodes) {
    const cats = new Set();
    nodes.forEach(n => cats.add(n.category || 0));
    const maxCat = Math.max(...cats, 0);
    const count = Math.max(maxCat + 1, CATEGORY_NAMES.length);
    return Array.from({ length: count }, (_, i) => ({
        name: CATEGORY_NAMES[i] || 'Entity Type ' + i
    }));
}

/**
 * 构建完整的 ECharts option
 */
function buildGraphOption(data) {
    const categories = data.categories && data.categories.length > 0
        ? data.categories
        : buildCategories(data.nodes);

    const links = (data.links || []).map(l => ({
        source: l.source,
        target: l.target,
        label: {
            show: true,
            formatter: relationMap[l.label?.formatter] || l.label?.formatter || ''
        }
    }));

    return {
        color: ['#ff4d4f', '#ffc069', '#73d13d', '#9254de', '#13c2c2', '#40a9ff', '#ff7a45', '#f5222d'],
        tooltip: {
            trigger: 'item',
            formatter: p => p.dataType === 'node'
                ? `名称：${p.data.name}`
                : `医学关联：${p.data.label?.formatter || ''}`
        },
        series: [{
            name: '健康知识网', type: 'graph', layout: 'force',
            data: data.nodes, links: links, categories: categories,
            roam: true,
            label: { show: true, position: 'right' },
            force: { repulsion: 500, edgeLength: 160 },
            lineStyle: { color: 'source', curveness: 0.1, width: 2 }
        }]
    };
}

/**
 * 将图谱数据渲染到 ECharts
 */
function renderGraph(data) {
    if (!data || !data.nodes || data.nodes.length === 0) return false;
    const option = buildGraphOption(data);
    myChart.setOption(option, true);  // true = notMerge，完全替换
    return true;
}

// 立即使用本地数据渲染，保证首屏不白屏
renderGraph(localGraphData);

// 后台尝试从 Neo4j 后端加载初始图谱（默认展示"高血压"）
(async function loadInitialGraph() {
    const data = await apiGet('/graph/data?entityName=' + encodeURIComponent('高血压') + '&depth=1');
    if (data) {
        const graphData = transformGraphData(data);
        if (graphData && graphData.nodes && graphData.nodes.length > 0) {
            const selected = selectDiverseNodes(graphData.nodes, 10);
            const selNames = new Set(selected.map(n => n.name));
            const filtered = {
                nodes: selected,
                links: (graphData.links || []).filter(l => selNames.has(l.source) && selNames.has(l.target))
            };
            currentGraphData = filtered;
            lastClickedNodeName = '高血压';
            renderGraph(filtered);
            updateLocalKeywordList(filtered.nodes);
        }
    }
})();

/**
 * 加载并渲染指定实体的图谱
 */
async function loadGraphForEntity(entityName, category) {
    let url = '/graph/data?entityName=' + encodeURIComponent(entityName) + '&depth=1';
    if (category != null && category !== '') url += '&category=' + encodeURIComponent(category);
    const data = await apiGet(url);
    if (data) {
        const graphData = transformGraphData(data);
        if (graphData && graphData.nodes && graphData.nodes.length > 0) {
            const selected = selectDiverseNodes(graphData.nodes, 10);
            const selNames = new Set(selected.map(n => n.name));
            const filtered = {
                nodes: selected,
                links: (graphData.links || []).filter(l => selNames.has(l.source) && selNames.has(l.target))
            };
            currentGraphData = filtered;
            lastClickedNodeName = entityName;
            renderGraph(filtered);
            updateLocalKeywordList(filtered.nodes);
            return true;
        }
    }
    return false;
}


// ==================== 3. 全局状态机模型 ====================
let currentMode = 'LOGIN';

let userState = {
    isLoggedIn: false,
    username: '',
    role: '访客用户',
    preferences: [],
    lastSearchedKeyword: '无',
    lastClickedNode: '无'
};

// 本地模拟账号（后端不可用时的降级方案）
const mockDatabaseUsers = {
    'admin': { password: '123', role: '科研人员', preferences: ['心血管系统', '临床安全用药'] }
};

// 本地建议词列表（动态更新）
let localKeywordList = localGraphData.nodes.map(n => n.name);

function updateLocalKeywordList(nodes) {
    if (nodes && nodes.length > 0) {
        const names = nodes.map(n => n.name).filter(Boolean);
        if (names.length > 0) {
            localKeywordList = names;
        }
    }
}

// 模拟推荐算法（后端不可用时的降级方案）
function generateMockIntelligenceFeed() {
    const mainPref = userState.preferences[0] || '医学综合前沿';
    return [
        { title: `【根据${userState.role}偏好检索推荐】关于《${mainPref}》领域下【${userState.lastSearchedKeyword}】的最新科研指南报告`, source: '《The Lancet (柳叶刀)》', url: 'https://www.thelancet.com' },
        { title: `【多维图谱足迹跟踪】针对您近期高频查看的实体【${userState.lastClickedNode}】的交叉关联医学文献推理分析`, source: '《Nature Medicine (自然医学)》', url: 'https://www.nature.com' }
    ];
}


// ==================== 4. 登录与注册控制流 ====================
const globalAuthCenter = document.getElementById('global-auth-center');
const mainAppContent = document.getElementById('main-app-content');
const authMainTitle = document.getElementById('auth-main-title');
const registerProfilePanel = document.getElementById('register-profile-panel');
const authActionBtn = document.getElementById('auth-action-btn');
const authStateToggle = document.getElementById('auth-state-toggle');
const userInfoDisplay = document.getElementById('user-info-display');
const checkboxes = document.getElementsByName('pref-tag');

// 偏好数校验拦截
checkboxes.forEach(box => {
    box.addEventListener('change', () => {
        if (document.querySelectorAll('input[name="pref-tag"]:checked').length > 3) {
            box.checked = false;
            alert('为了推荐算力的精准匹配，偏好最多选择 3 项！');
        }
    });
});

// 核心功能：切换登录与注册模式
const toggleAuthMode = () => {
    if (currentMode === 'LOGIN') {
        currentMode = 'REGISTER';
        authMainTitle.innerText = '全新科研账户注册';
        authMainTitle.style.color = '#52c41a';
        registerProfilePanel.style.display = 'block';
        authActionBtn.style.background = '#52c41a';
        authActionBtn.innerText = '完成注册并初始化系统';
        authStateToggle.innerText = '已有科研账号？立即返回登录';
    } else {
        currentMode = 'LOGIN';
        authMainTitle.innerText = '科研账户登录认证';
        authMainTitle.style.color = '#0050b3';
        registerProfilePanel.style.display = 'none';
        authActionBtn.style.background = '#40a9ff';
        authActionBtn.innerText = '验证登录';
        authStateToggle.innerText = '没有账号？立即注册新科研账户';
    }
};

authStateToggle.addEventListener('click', toggleAuthMode);

// 点击核心验证/注册按钮
authActionBtn.addEventListener('click', async () => {
    const usernameInput = document.getElementById('auth-username').value.trim();
    const passwordInput = document.getElementById('auth-password').value.trim();

    if (!usernameInput || !passwordInput) {
        alert('安全起见，账号和密码均不能为空！');
        return;
    }

    if (currentMode === 'LOGIN') {
        // ---------- 登录流程 ----------
        // 优先调用后端 API
        const result = await apiPost('/user/login', {
            username: usernameInput,
            password: passwordInput
        });

        if (result === null) {
            // ★ 网络不通 → 降级到本地模拟登录
            const foundUser = mockDatabaseUsers[usernameInput];
            if (foundUser && foundUser.password === passwordInput) {
                enterMainSystem(usernameInput, foundUser.role, foundUser.preferences);
            } else if (!foundUser && usernameInput !== 'admin') {
                enterMainSystem(usernameInput, '科研人员', ['心血管系统']);
            } else {
                alert('账户核验失败：密码不正确，请重新输入！');
            }
        } else if (result.error) {
            // 后端返回了业务错误（如密码错误）
            alert(result.error);
        } else {
            // 后端登录成功
            const user = result.data;
            enterMainSystem(user.username, user.role, user.preferences || []);
        }
    } else {
        // ---------- 注册流程 ----------
        const selectedRole = document.getElementById('role-select').value;
        const selectedPrefs = Array.from(
            document.querySelectorAll('input[name="pref-tag"]:checked')
        ).map(cb => cb.value);

        // 优先调用后端 API
        const result = await apiPost('/user/register', {
            username: usernameInput,
            password: passwordInput,
            role: selectedRole,
            preferences: selectedPrefs
        });

        if (result === null) {
            // ★ 网络不通 → 降级到本地模拟注册
            mockDatabaseUsers[usernameInput] = {
                password: passwordInput,
                role: selectedRole,
                preferences: selectedPrefs
            };
            alert('🎉 恭喜！科研凭证已保存至本地（后端服务暂未连接，数据仅存在于当前会话）。');
            enterMainSystem(usernameInput, selectedRole, selectedPrefs);
        } else if (result.error) {
            // 后端返回了业务错误（如用户名已存在）
            alert(result.error);
        } else {
            // 后端注册成功，数据已写入 MySQL
            alert('🎉 恭喜！科研凭证已成功注册并同步写入MySQL数据库，冷启动画像已完成。');
            enterMainSystem(usernameInput, selectedRole, selectedPrefs);
        }
    }
});

// 验证放行：关闭遮罩层，展开真实业务主系统
function enterMainSystem(username, role, preferences) {
    userState.isLoggedIn = true;
    userState.username = username;
    userState.role = role;
    userState.preferences = preferences;

    globalAuthCenter.style.display = 'none';
    mainAppContent.style.display = 'block';

    userInfoDisplay.innerHTML = `
        <span style="color:#52c41a; font-weight:bold;">👤 凭证已授信：${userState.username} [${userState.role}]</span>
        <span style="font-size:12px;color:#666;margin-left:10px;">研究领域：${userState.preferences.join('、') || '全科关注'}</span>
        <span id="live-traits" style="font-size:12px;color:#1890ff;margin-left:15px;background:#e6f7ff;padding:2px 6px;border-radius:4px;">足迹：搜过[${userState.lastSearchedKeyword}] | 点过[${userState.lastClickedNode}]</span>
        <button id="logout-btn" style="margin-left:10px;padding:2px 8px;font-size:12px;cursor:pointer;">安全退出</button>
    `;

    document.getElementById('logout-btn').addEventListener('click', () => { location.reload(); });

    renderHistoryPanel();
    updateUserIntelligenceFeed();
    myChart.resize();
}

// 更新足迹显示
function updateFootprint() {
    const traitsEl = document.getElementById('live-traits');
    if (traitsEl) {
        traitsEl.innerText = `足迹：搜过[${userState.lastSearchedKeyword}] | 点过[${userState.lastClickedNode}]`;
        traitsEl.style.cssText = 'font-size:12px;color:#1890ff;margin-left:15px;background:#e6f7ff;padding:2px 6px;border-radius:4px;';
    }
}


// ==================== 5. 推荐、查询与特征捕获逻辑 ====================
async function updateUserIntelligenceFeed() {
    const cardContainer = document.getElementById('recommend-cards');
    if (!userState.isLoggedIn) return;

    cardContainer.innerHTML = `<p style="color:#888; font-size:14px;">智能化Feed流重组计算中...</p>`;

    // 优先调用后端推荐 API
    const feed = await apiGet('/recommend/user-feed?username=' + encodeURIComponent(userState.username));
    if (feed && Array.isArray(feed) && feed.length > 0) {
        renderRecommendCards(cardContainer, feed);
        return;
    }

    // ★ 降级到本地模拟推荐
    const mockFeed = generateMockIntelligenceFeed();
    renderRecommendCards(cardContainer, mockFeed);
}

function renderRecommendCards(container, articles) {
    container.innerHTML = articles.map(art => `
        <div class="card" data-url="${art.url || '#'}">
            <h4>${art.title}</h4>
            <p style="color: #0050b3; font-size: 12px; margin-bottom:0;">精准学术源：${art.source} 🔗 <span style="float:right; color:#52c41a; font-weight:bold;">三维权重融合度 99.8%</span></p>
        </div>
    `).join('');

    container.querySelectorAll('.card').forEach(card => {
        card.addEventListener('click', function () {
            if (this.getAttribute('data-url') !== '#') {
                window.open(this.getAttribute('data-url'), '_blank');
            }
        });
    });
}


// ==================== 搜索历史管理 ====================
const MAX_HISTORY = 10;
const HISTORY_KEY = 'health_kg_search_history';

function getSearchHistory() {
    try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; }
    catch (e) { return []; }
}
function saveSearchHistory(list) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list)); } catch (e) {}
}
function addToHistory(keyword, category) {
    const list = getSearchHistory();
    const idx = list.findIndex(h => h.keyword === keyword && h.category === (category || ''));
    if (idx !== -1) list.splice(idx, 1);
    list.unshift({ keyword, category: category != null ? category : '' });
    if (list.length > MAX_HISTORY) list.length = MAX_HISTORY;
    saveSearchHistory(list);
    renderHistoryPanel();
}
function clearHistory() {
    localStorage.removeItem(HISTORY_KEY);
    renderHistoryPanel();
}
function renderHistoryPanel() {
    const panel = document.getElementById('history-panel');
    const tagsEl = document.getElementById('history-tags');
    if (!panel || !tagsEl) return;
    const list = getSearchHistory();
    if (list.length === 0) { panel.classList.add('history-hidden'); return; }
    panel.classList.remove('history-hidden');
    const catLabels = ['疾病', '症状', '药品', '检查', '治疗'];
    tagsEl.innerHTML = list.map(h => {
        const catBadge = (h.category !== '' && h.category != null)
            ? `<span style="font-size:10px;color:#888;">[${catLabels[h.category] || h.category}]</span>`
            : '';
        return `<span class="history-tag" data-kw="${h.keyword}" data-cat="${h.category}">${catBadge}${h.keyword}<span class="history-tag-del">&times;</span></span>`;
    }).join('');
    tagsEl.querySelectorAll('.history-tag').forEach(tag => {
        tag.addEventListener('click', function(e) {
            if (e.target.classList.contains('history-tag-del')) {
                const kw = this.getAttribute('data-kw');
                const cat = this.getAttribute('data-cat');
                const list = getSearchHistory().filter(h => !(h.keyword === kw && h.category === cat));
                saveSearchHistory(list);
                renderHistoryPanel();
                return;
            }
            const kw = this.getAttribute('data-kw');
            const cat = this.getAttribute('data-cat');
            document.getElementById('search-input').value = kw;
            document.getElementById('category-filter').value = cat;
            triggerSearch(kw, cat || null);
        });
    });
}

// ==================== 6. 搜索与建议 ====================
let suggestDebounceTimer = null;

async function triggerSearch(keyword, category) {
    if (!keyword || !keyword.trim()) return;
    keyword = keyword.trim();
    if (category === '') category = null;

    const loaded = await loadGraphForEntity(keyword, category);
    if (loaded) {
        updateDetails(keyword);
        userState.lastSearchedKeyword = keyword;
        addToHistory(keyword, category);
        updateFootprint();
        updateUserIntelligenceFeed();
        return;
    }

    // ★ 降级：在本地数据中搜索（按分类过滤）
    let matchIdx = localGraphData.nodes.findIndex(n => n.name === keyword);
    if (matchIdx !== -1 && category != null && localGraphData.nodes[matchIdx].category !== parseInt(category)) {
        matchIdx = -1;
    }
    if (matchIdx !== -1) {
        myChart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: matchIdx });
        updateDetails(keyword);
        userState.lastSearchedKeyword = keyword;
        addToHistory(keyword, category);
        updateFootprint();
        updateUserIntelligenceFeed();
    } else {
        const tip = category != null ? '（当前筛选条件下）' : '';
        alert(`系统暂未收录【${keyword}】${tip}。`);
    }
}

// 搜索建议（带防抖，支持分类筛选）
async function loadSuggestions(keyword, category) {
    let url = '/search/suggest?keyword=' + encodeURIComponent(keyword);
    if (category != null && category !== '') url += '&category=' + encodeURIComponent(category);
    const data = await apiGet(url);
    if (data && Array.isArray(data) && data.length > 0) return data;
    return null;
}

function renderSuggestions(keyword, apiResults, category) {
    const suggestPanel = document.getElementById('suggest-panel');
    if (apiResults) {
        suggestPanel.innerHTML = apiResults.slice(0, 10).map(i => `<div class="suggest-item">${i}</div>`).join('');
        suggestPanel.classList.remove('suggest-hidden');
        return;
    }

    // ★ 降级到本地过滤（支持分类筛选）
    let matches = localKeywordList.filter(k => k.includes(keyword));
    if (category != null && category !== '') {
        const catMap = {};
        localGraphData.nodes.forEach(n => { catMap[n.name] = n.category; });
        matches = matches.filter(k => catMap[k] === parseInt(category));
    }
    if (matches.length > 0) {
        suggestPanel.innerHTML = matches.slice(0, 10).map(i => `<div class="suggest-item">${i}</div>`).join('');
        suggestPanel.classList.remove('suggest-hidden');
    } else {
        suggestPanel.classList.add('suggest-hidden');
    }
}

const searchInput = document.getElementById('search-input');
const suggestPanel = document.getElementById('suggest-panel');
const categoryFilter = document.getElementById('category-filter');

// 分类筛选变更时，如果输入框已有内容则重新触发建议
categoryFilter.addEventListener('change', () => {
    const v = searchInput.value.trim();
    if (!v) return;
    clearTimeout(suggestDebounceTimer);
    suggestDebounceTimer = setTimeout(async () => {
        const apiResults = await loadSuggestions(v, categoryFilter.value);
        renderSuggestions(v, apiResults, categoryFilter.value);
    }, 200);
});

searchInput.addEventListener('input', (e) => {
    const v = e.target.value.trim();
    if (!v) {
        suggestPanel.classList.add('suggest-hidden');
        return;
    }
    clearTimeout(suggestDebounceTimer);
    suggestDebounceTimer = setTimeout(async () => {
        const apiResults = await loadSuggestions(v, categoryFilter.value);
        renderSuggestions(v, apiResults, categoryFilter.value);
    }, 200);
});

suggestPanel.addEventListener('click', (e) => {
    if (e.target.classList.contains('suggest-item')) {
        searchInput.value = e.target.innerText;
        suggestPanel.classList.add('suggest-hidden');
        triggerSearch(searchInput.value, categoryFilter.value);
    }
});

document.getElementById('search-btn').addEventListener('click', () => {
    triggerSearch(searchInput.value, categoryFilter.value);
});

document.getElementById('clear-history-btn').addEventListener('click', clearHistory);


// ==================== 7. 实体详情面板 ====================
async function updateDetails(nodeName) {
    const detailContent = document.getElementById('detail-content');
    detailContent.innerHTML = `<h4>【${nodeName}】</h4><p style="color:#2b85e4;">⏳ 关联检索中...</p>`;

    // 优先从后端获取详情
    const detail = await apiGet('/entity/detail?name=' + encodeURIComponent(nodeName));
    if (detail) {
        renderDetailsHTML(detailContent, {
            name: detail.name || nodeName,
            category: (detail.category != null) ? detail.category : '',
            definition: detail.definition || '暂无',
            indications: detail.indications || '暂无',
            badReactions: detail.badReactions || ''
        });
        return;
    }

    // ★ 降级到本地备份
    if (localBackupDetails[nodeName]) {
        renderDetailsHTML(detailContent, localBackupDetails[nodeName]);
    } else {
        detailContent.innerHTML = `<h4>【${nodeName}】</h4><p style="color:#999;">暂无该实体的详细医学数据。</p>`;
    }
}

function renderDetailsHTML(c, d) {
    const catDisplay = (typeof d.category === 'number' && CATEGORY_NAMES[d.category])
        ? CATEGORY_NAMES[d.category]
        : (d.category || '未知');
    c.innerHTML = `<h4>【${d.name}】</h4><b>实体类型：</b>${catDisplay}<br><br><b>医学定义：</b><br>${d.definition || '暂无'}<br><br><b>临床指南：</b><br>${d.indications || '暂无'}`;
}

// 图谱节点点击事件：查询详情 + 增量扩展邻居节点
myChart.on('click', async function (params) {
    if (params.dataType === 'node') {
        const nodeName = params.name;
        updateDetails(nodeName);
        userState.lastClickedNode = nodeName;
        updateFootprint();
        updateUserIntelligenceFeed();

        // 增量扩展：调 expand 接口获取被点击节点的邻居
        if (currentGraphData && currentGraphData.nodes) {
            const existingNames = new Set(currentGraphData.nodes.map(n => n.name));

            // 如果点击的是不同于上一级的节点，裁剪旧节点：
            // 保留 上一级节点及其邻居 + 当前节点及其邻居，移除无关的祖辈邻居
            if (lastClickedNodeName && nodeName !== lastClickedNodeName) {
                const parentNeighbors = new Set();
                const clickedNeighbors = new Set();
                currentGraphData.links.forEach(l => {
                    if (l.source === lastClickedNodeName) parentNeighbors.add(l.target);
                    if (l.target === lastClickedNodeName) parentNeighbors.add(l.source);
                    if (l.source === nodeName) clickedNeighbors.add(l.target);
                    if (l.target === nodeName) clickedNeighbors.add(l.source);
                });
                const keepNames = new Set([lastClickedNodeName, nodeName, ...parentNeighbors, ...clickedNeighbors]);
                const trimmedNodes = currentGraphData.nodes.filter(n => keepNames.has(n.name));
                const trimmedLinks = currentGraphData.links.filter(l =>
                    keepNames.has(l.source) && keepNames.has(l.target)
                );
                currentGraphData = { nodes: trimmedNodes, links: trimmedLinks };
            }

            const newExistingNames = new Set(currentGraphData.nodes.map(n => n.name));
            const expandUrl = '/graph/expand?entityName=' + encodeURIComponent(nodeName)
                + '&exclude=' + encodeURIComponent([...newExistingNames].join(','));
            const expandData = await apiGet(expandUrl);
            if (expandData) {
                const newPart = transformGraphData(expandData);
                if (newPart && newPart.nodes && newPart.nodes.length > 0) {
                    const trulyNew = newPart.nodes.filter(n => !newExistingNames.has(n.name));
                    const selected = selectDiverseNodes(trulyNew, 8);
                    const selNames = new Set(selected.map(n => n.name));
                    const selLinks = (newPart.links || []).filter(l =>
                        selNames.has(l.source) || selNames.has(l.target)
                    );
                    const mergedNodes = [...currentGraphData.nodes, ...selected];
                    const mergedLinks = [...currentGraphData.links, ...selLinks];
                    currentGraphData = { nodes: mergedNodes, links: mergedLinks };
                    lastClickedNodeName = nodeName;
                    renderGraph(currentGraphData);
                }
            }
        }
    }
});


// ==================== 8. 窗口事件 ====================
window.addEventListener('resize', () => myChart.resize());
