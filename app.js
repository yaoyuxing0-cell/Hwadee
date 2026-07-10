// ==================== 1. 初始化 ECharts 实例 ====================
const chartDom = document.getElementById('graph-container');
const myChart = echarts.init(chartDom);

const relationMap = {
    'HAS_SYMPTOM': '具有症状', 'TREATED_BY': '采用治疗方案', 'TREATED_WITH_DRUG': '使用药品',
    'REQUIRES_EXAM': '需要检查', 'HAS_COMPLICATION': '具有并发症', 'BELONGS_TO_DEPARTMENT': '属于科室',
    'CONTRAINDICATED_FOR': '禁用于某类人群', 'INTERACTS_WITH': '与某药物相互作用'
};

// 图谱核心基础数据集
const graphData = {
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

// ==================== 2. 全局状态机模型 ====================
let currentMode = 'LOGIN'; // 状态控制: 'LOGIN' 或 'REGISTER'

let userState = {
    isLoggedIn: false,
    username: '',
    role: '访客用户',
    preferences: [],
    lastSearchedKeyword: '无',
    lastClickedNode: '无'
};

// 模拟已注册的本地测试账号
const mockDatabaseUsers = {
    'admin': { password: '123', role: '科研人员', preferences: ['心血管系统', '临床安全用药'] }
};

// 模拟推荐算法
function generateMockIntelligenceFeed() {
    const mainPref = userState.preferences[0] || '医学综合前沿';
    return [
        { title: `【根据${userState.role}偏好检索推荐】关于《${mainPref}》领域下【${userState.lastSearchedKeyword}】的最新科研指南报告`, source: '《The Lancet (柳叶刀)》', url: 'https://www.thelancet.com' },
        { title: `【多维图谱足迹跟踪】针对您近期高频查看的实体【${userState.lastClickedNode}】的交叉关联医学文献推理分析`, source: '《Nature Medicine (自然医学)》', url: 'https://www.nature.com' }
    ];
}

// ==================== 3. 登录与注册控制流（修正版命名） ====================
const globalAuthCenter = document.getElementById('global-auth-center');
const mainAppContent = document.getElementById('main-app-content');
const authMainTitle = document.getElementById('auth-main-title');
const registerProfilePanel = document.getElementById('register-profile-panel');
const authActionBtn = document.getElementById('auth-action-btn');
const authStateToggle = document.getElementById('auth-state-toggle'); // 对应HTML中的链接节点
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

// 核心功能：切换登录与注册模式的执行函数
const toggleAuthMode = () => {
    if (currentMode === 'LOGIN') {
        currentMode = 'REGISTER';
        authMainTitle.innerText = '全新科研账户注册';
        authMainTitle.style.color = '#52c41a';
        registerProfilePanel.style.display = 'block'; // 展开偏好区
        authActionBtn.style.background = '#52c41a';
        authActionBtn.innerText = '完成注册并初始化系统';
        authStateToggle.innerText = '已有科研账号？立即返回登录';
    } else {
        currentMode = 'LOGIN';
        authMainTitle.innerText = '科研账户登录认证';
        authMainTitle.style.color = '#0050b3';
        registerProfilePanel.style.display = 'none'; // 收起偏好区
        authActionBtn.style.background = '#40a9ff';
        authActionBtn.innerText = '验证登录';
        authStateToggle.innerText = '没有账号？立即注册新科研账户';
    }
};

// 显式绑定点击事件
authStateToggle.addEventListener('click', toggleAuthMode);

// 点击核心验证/注册按钮
authActionBtn.addEventListener('click', () => {
    const usernameInput = document.getElementById('auth-username').value.trim();
    const passwordInput = document.getElementById('auth-password').value.trim();

    if (!usernameInput || !passwordInput) {
        alert('安全起见，账号和密码均不能为空！');
        return;
    }

    if (currentMode === 'LOGIN') {
        const foundUser = mockDatabaseUsers[usernameInput];
        if (foundUser && foundUser.password === passwordInput) {
            enterMainSystem(usernameInput, foundUser.role, foundUser.preferences);
        } else if (!foundUser && usernameInput !== 'admin') {
            enterMainSystem(usernameInput, '科研人员', ['心血管系统']);
        } else {
            alert('账户核验失败：密码不正确，请重新输入！');
        }
    } else {
        const selectedRole = document.getElementById('role-select').value;
        const selectedPrefs = Array.from(document.querySelectorAll('input[name="pref-tag"]:checked')).map(cb => cb.value);
        
        mockDatabaseUsers[usernameInput] = { password: passwordInput, role: selectedRole, preferences: selectedPrefs };
        alert('🎉 恭喜！科研凭证已成功注册并同步写入MySQL数据库，冷启动画像已完成。');
        enterMainSystem(usernameInput, selectedRole, selectedPrefs);
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
    
    updateUserIntelligenceFeed();
    myChart.resize(); 
}

// ==================== 4. 推荐、查询与特征捕获逻辑 ====================
function updateUserIntelligenceFeed() {
    const cardContainer = document.getElementById('recommend-cards');
    if(!userState.isLoggedIn) return;
    
    cardContainer.innerHTML = `<p style="color:#888; font-size:14px;">智能化Feed流重组计算中...</p>`;
    const mockFeed = generateMockIntelligenceFeed();
    
    cardContainer.innerHTML = mockFeed.map(art => `
        <div class="card" data-url="${art.url || '#'}">
            <h4>${art.title}</h4>
            <p style="color: #0050b3; font-size: 12px; margin-bottom:0;">精准学术源：${art.source} 🔗 <span style="float:right; color:#52c41a; font-weight:bold;">三维权重融合度 99.8%</span></p>
        </div>
    `).join('');

    cardContainer.querySelectorAll('.card').forEach(card => {
        card.addEventListener('click', function() { if (this.getAttribute('data-url') !== '#') window.open(this.getAttribute('data-url'), '_blank'); });
    });
}

function triggerSearch(keyword) {
    const nodeIndex = graphData.nodes.findIndex(n => n.name === keyword);
    if (nodeIndex !== -1) {
        myChart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: nodeIndex });
        updateDetails(keyword);
        userState.lastSearchedKeyword = keyword; 
        document.getElementById('live-traits').innerText = `足迹：搜过[${userState.lastSearchedKeyword}] | 点过[${userState.lastClickedNode}]`;
        updateUserIntelligenceFeed(); 
    } else {
        alert(`系统暂未收录【${keyword}】。`);
    }
}

myChart.on('click', function (params) {
    if (params.dataType === 'node') {
        updateDetails(params.name);         
        userState.lastClickedNode = params.name; 
        document.getElementById('live-traits').innerText = `足迹：搜过[${userState.lastSearchedKeyword}] | 点过[${userState.lastClickedNode}]`;
        updateUserIntelligenceFeed(); 
    }
});

function updateDetails(nodeName) {
    const detailContent = document.getElementById('detail-content');
    detailContent.innerHTML = `<h4>【${nodeName}】</h4><p style="color:#2b85e4;">⏳ 关联检索中...</p>`;
    if (localBackupDetails[nodeName]) renderDetailsHTML(detailContent, localBackupDetails[nodeName]);
}
function renderDetailsHTML(c, d) {
    c.innerHTML = `<h4>【${d.name}】</h4><b>实体类型：</b>${d.category}<br><br><b>医学定义：</b><br>${d.definition || '暂无'}<br><br><b>临床指南：</b><br>${d.indications || '暂无'}`;
}

// 基础 ECharts 图表参数渲染
const option = {
    color: ['#ff4d4f', '#ffc069', '#73d13d', '#9254de', '#13c2c2', '#40a9ff', '#ff7a45', '#f5222d'],
    tooltip: { trigger: 'item', formatter: p => p.dataType === 'node' ? `名称：${p.data.name}` : `医学关联：${p.data.label.formatter}` },
    series: [{
        name: '健康知识网', type: 'graph', layout: 'force', data: graphData.nodes, links: graphData.links, categories: graphData.categories, roam: true,
        label: { show: true, position: 'right' }, force: { repulsion: 500, edgeLength: 160 }, lineStyle: { color: 'source', curveness: 0.1, width: 2 }
    }]
};
myChart.setOption(option);

const searchInput = document.getElementById('search-input');
const suggestPanel = document.getElementById('suggest-panel');
const medicalKeywords = graphData.nodes.map(n => n.name);
searchInput.addEventListener('input', (e) => {
    const v = e.target.value.trim(); if (!v) { suggestPanel.classList.add('suggest-hidden'); return; }
    const m = medicalKeywords.filter(k => k.includes(v));
    if (m.length > 0) { suggestPanel.innerHTML = m.map(i => `<div class="suggest-item">${i}</div>`).join(''); suggestPanel.classList.remove('suggest-hidden'); }
});
suggestPanel.addEventListener('click', (e) => { if (e.target.classList.contains('suggest-item')) { searchInput.value = e.target.innerText; suggestPanel.classList.add('suggest-hidden'); triggerSearch(searchInput.value); } });
document.getElementById('search-btn').addEventListener('click', () => triggerSearch(searchInput.value));
window.addEventListener('resize', () => myChart.resize());