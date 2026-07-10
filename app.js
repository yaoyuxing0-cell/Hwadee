// ==================== 1. 初始化 ECharts 实例 ====================
const chartDom = document.getElementById('graph-container');
const myChart = echarts.init(chartDom);

// ==================== 2. 关系类型中英映射映射表 ====================
const relationMap = {
    'HAS_SYMPTOM': '具有症状',
    'TREATED_BY': '采用治疗方案',
    'TREATED_WITH_DRUG': '使用药品',
    'REQUIRES_EXAM': '需要检查',
    'HAS_COMPLICATION': '具有并发症',
    'BELONGS_TO_DEPARTMENT': '属于科室',
    'CONTRAINDICATED_FOR': '禁用于某类人群',
    'INTERACTS_WITH': '与某药物相互作用'
};

// ==================== 3. 严格对齐组长要求的核心数据集 (ECharts 专用) ====================
const graphData = {
    // 节点类型对应 category 索引：0:Disease, 1:Symptom, 2:Drug, 3:Examination, 4:Treatment, 5:Department, 6:Complication, 7:Population
    nodes: [
        { name: '高血压', category: 0, symbolSize: 65 },
        { name: '头晕', category: 1, symbolSize: 45 },
        { name: '心悸', category: 1, symbolSize: 45 },
        { name: '硝苯地平', category: 2, symbolSize: 48 },
        { name: '卡托普利', category: 2, symbolSize: 48 },
        { name: '心电图', category: 3, symbolSize: 45 },
        { name: '低盐饮食', category: 4, symbolSize: 45 },
        { name: '心血管内科', category: 5, symbolSize: 50 },
        { name: '脑卒中', category: 6, symbolSize: 45 },
        { name: '孕妇', category: 7, symbolSize: 45 },
        { name: '阿司匹林', category: 2, symbolSize: 45 }
    ],
    links: [
        { source: '高血压', target: '头晕', label: { show: true, formatter: relationMap['HAS_SYMPTOM'] } },
        { source: '高血压', target: '心悸', label: { show: true, formatter: relationMap['HAS_SYMPTOM'] } },
        { source: '高血压', target: '硝苯地平', label: { show: true, formatter: relationMap['TREATED_WITH_DRUG'] } },
        { source: '高血压', target: '卡托普利', label: { show: true, formatter: relationMap['TREATED_WITH_DRUG'] } },
        { source: '高血压', target: '心电图', label: { show: true, formatter: relationMap['REQUIRES_EXAM'] } },
        { source: '高血压', target: '低盐饮食', label: { show: true, formatter: relationMap['TREATED_BY'] } },
        { source: '高血压', target: '心血管内科', label: { show: true, formatter: relationMap['BELONGS_TO_DEPARTMENT'] } },
        { source: '高血压', target: '脑卒中', label: { show: true, formatter: relationMap['HAS_COMPLICATION'] } },
        { source: '硝苯地平', target: '孕妇', label: { show: true, formatter: relationMap['CONTRAINDICATED_FOR'] } },
        { source: '硝苯地平', target: '阿司匹林', label: { show: true, formatter: relationMap['INTERACTS_WITH'] } }
    ],
    categories: [
        { name: 'Disease (疾病)' },       // 0
        { name: 'Symptom (症状)' },       // 1
        { name: 'Drug (药品)' },          // 2
        { name: 'Examination (检查)' },   // 3
        { name: 'Treatment (治疗)' },     // 4
        { name: 'Department (科室)' },    // 5
        { name: 'Complication (并发症)' },// 6
        { name: 'Population (特殊人群)' }  // 7
    ]
};

// 本地离线保底医学详情数据集
const localBackupDetails = {
    '高血压': { name: '高血压', category: 'Disease (疾病)', definition: '以体循环动脉血压增高为主要特征的临床综合征。', indications: '需结合临床指南定期监测，防止靶器官损伤。', badReactions: '早期多无症状，偶有头晕、头痛、心悸等。' },
    '硝苯地平': { name: '硝苯地平', category: 'Drug (药品)', definition: '钙通道阻滞剂（CCB），通过舒张外周血管达到降压目的。', indications: '用于治疗高血压、慢性稳定性心绞痛。', badReactions: '可能引发面部潮红、下肢水肿、头痛。' },
    '头晕': { name: '头晕', category: 'Symptom (症状)', definition: '空间定向觉障碍，伴有轻度站立不稳。', indications: '常见原因为脑供血不足、高血压或颈椎病。', badReactions: '建议行动态血压监测及经颅多普勒超声检查。' },
    '心血管内科': { name: '心血管内科', category: 'Department (科室)', definition: '专注于内科领域的循环系统疾病诊疗中心。', indications: '高血压、冠心病、心力衰竭、心律失常等。', badReactions: '涉及跨学科会诊时需关联神经内科、内分泌科。' },
    '孕妇': { name: '孕妇', category: 'Population (特殊人群)', definition: '处于妊娠期的特殊女性群体，对药物极其敏感。', indications: '关注妊娠期高血压综合征（PIH）的预防。', badReactions: '多类降压药物有致畸风险，硝苯地平必须在专科医生指导下严密监护使用。' }
};

// 本地离线保底个性化推荐池（包含演示用真实可跳转URL）
const localBackupRecommendations = {
    '高血压': [
        { title: '高血压患者的日常低盐膳食配比与生活调理', source: '健康科普机构', url: 'https://baike.baidu.com/item/%E9%AB%98%E8%A1%80%E5%8E%8B' },
        { title: '世界卫生组织（WHO）高血压防治核心指南', source: '国际医学科研文献', url: 'https://www.who.int' }
    ],
    '硝苯地平': [
        { title: '长期服用硝苯地平缓释片，必须警惕的3个副作用', source: '临床药学中心', url: 'https://baike.baidu.com/item/%E7%A1%9D%E8%8B%AF%E5%9C%B0%E5%B9%B3' }
    ],
    '孕妇': [
        { title: '妊娠期高血压如何安全用药？听听产科专家怎么说', source: '妇幼保健院', url: 'https://www.cnki.net' }
    ],
    'default': [
        { title: '日常如何通过结构化膳食提升人体免疫屏障', source: '公共健康科普库', url: 'https://www.gov.cn' },
        { title: '基于知识图谱技术的智能全能医学问答系统前景展望', source: '系统科研辅助中心', url: 'https://github.com/yeeeqichen/MedicalKG' }
    ]
};

// ==================== 4. 基础 ECharts 关系图配置 ====================
const option = {
    color: ['#ff4d4f', '#ffc069', '#73d13d', '#9254de', '#13c2c2', '#40a9ff', '#ff7a45', '#f5222d'],
    tooltip: {
        trigger: 'item',
        formatter: function (params) {
            if (params.dataType === 'node') {
                return `<b>名称：</b>${params.data.name}<br/><b>标签：</b>${graphData.categories[params.data.category].name}`;
            } else if (params.dataType === 'edge') {
                return `<b>医学关联：</b>${params.data.label.formatter}`;
            }
        }
    },
    legend: [{
        data: graphData.categories.map(x => x.name),
        orient: 'vertical',
        left: 'left',
        top: 'top'
    }],
    series: [
        {
            name: '健康知识网',
            type: 'graph',
            layout: 'force',
            data: graphData.nodes,
            links: graphData.links,
            categories: graphData.categories,
            roam: true,
            label: { show: true, position: 'right', color: '#333' },
            force: { repulsion: 500, edgeLength: 160, gravity: 0.05 },
            lineStyle: { color: 'source', curveness: 0.1, width: 2 },
            emphasis: { focus: 'adjacency', lineStyle: { width: 4 } }
        }
    ]
};
myChart.setOption(option);

// ==================== 5. 核心前端交互与接口调用逻辑 ====================

// 功能一：知识检索与输入联想
const searchInput = document.getElementById('search-input');
const suggestPanel = document.getElementById('suggest-panel');
const searchBtn = document.getElementById('search-btn');
const medicalKeywords = graphData.nodes.map(n => n.name); 

searchInput.addEventListener('input', (e) => {
    const value = e.target.value.trim();
    if (!value) {
        suggestPanel.classList.add('suggest-hidden');
        return;
    }
    const matches = medicalKeywords.filter(k => k.includes(value));
    if (matches.length > 0) {
        suggestPanel.innerHTML = matches.map(item => `<div class="suggest-item">${item}</div>`).join('');
        suggestPanel.classList.remove('suggest-hidden');
    } else {
        suggestPanel.classList.add('suggest-hidden');
    }
});

suggestPanel.addEventListener('click', (e) => {
    if (e.target.classList.contains('suggest-item')) {
        searchInput.value = e.target.innerText;
        suggestPanel.classList.add('suggest-hidden');
        triggerSearch(searchInput.value);
    }
});

function triggerSearch(keyword) {
    const nodeIndex = graphData.nodes.findIndex(n => n.name === keyword);
    if (nodeIndex !== -1) {
        myChart.dispatchAction({ type: 'highlight', seriesIndex: 0, dataIndex: nodeIndex });
        updateDetails(keyword);
        updateRecommendations(keyword);
    } else {
        alert(`系统暂未从文献中抽取到【${keyword}】。后续将结合NLP的BERT与OpenIE为您精准提取！`);
    }
}
searchBtn.addEventListener('click', () => triggerSearch(searchInput.value));


// 功能二：关联查询 (动态对接后端医学详情接口)
function updateDetails(nodeName) {
    const detailContent = document.getElementById('detail-content');
    detailContent.innerHTML = `<h4>【${nodeName}】</h4><p style="color:#2b85e4;">⏳ 正在从后台医疗图数据库检索关联知识...</p>`;

    const apiUrl = `http://localhost:8080/api/v1/entity/detail?name=${encodeURIComponent(nodeName)}`;

    fetch(apiUrl)
        .then(response => {
            if (!response.ok) throw new Error('接口未联通');
            return response.json();
        })
        .then(res => {
            if (res.code === 200 && res.data) {
                renderDetailsHTML(detailContent, res.data, false);
            } else {
                detailContent.innerHTML = `<h4>【${nodeName}】</h4><p style="color:#f5222d;">错误提示: ${res.message}</p>`;
            }
        })
        .catch(error => {
            if (localBackupDetails[nodeName]) {
                renderDetailsHTML(detailContent, localBackupDetails[nodeName], true);
            } else {
                detailContent.innerHTML = `<h4>【${nodeName}】</h4><p style="color:#faad14; font-size:12px;">⚠️ 接口未检测到。该节点已导入Neo4j，详细文本正在融合中...</p>`;
            }
        });
}

function renderDetailsHTML(container, data, isMock) {
    const mockBadge = isMock ? `<p style="color:#faad14; font-size:12px; margin:0 0 10px 0;">⚠️ 提示：运行在本地保底模拟模式。</p>` : '';
    container.innerHTML = `
        <h4>【${data.name}】</h4>
        ${mockBadge}
        <b>实体类型：</b>${data.category}<br><br>
        <b>医学定义/概念：</b><br>${data.definition || '暂无'}<br><br>
        <b>临床指南/适应症/诊疗范围：</b><br>${data.indications || '暂无'}<br><br>
        <b>注意事项/不良反应/配伍禁忌：</b><br>${data.badReactions || '暂无'}
    `;
}


// 功能三：个性化推荐 (动态对接后端推荐接口 + 新窗口链接跳转)
function updateRecommendations(entityName) {
    const cardContainer = document.getElementById('recommend-cards');
    cardContainer.innerHTML = `<p style="color:#888; font-size:14px;">正在智能计算推荐知识流...</p>`;

    // 后端推荐真实 API 路径
    const apiUrl = `http://localhost:8080/api/v1/recommend/articles?entityName=${encodeURIComponent(entityName)}`;

    fetch(apiUrl)
        .then(response => {
            if (!response.ok) throw new Error('推荐接口未连接');
            return response.json();
        })
        .then(res => {
            if (res.code === 200 && res.data) {
                renderRecommendCards(cardContainer, res.data);
            }
        })
        .catch(error => {
            // 保底机制：若后端没写好推荐接口，直接读本地带链接的保底数据
            const backupData = localBackupRecommendations[entityName] || localBackupRecommendations['default'];
            renderRecommendCards(cardContainer, backupData);
        });
}

// 辅助函数：负责生成推荐卡片，并绑定点击跳转事件
function renderRecommendCards(container, articles) {
    if (!articles || articles.length === 0) {
        container.innerHTML = `<p style="color:#999;">暂无相关科普推荐。</p>`;
        return;
    }
    
    // 生成卡片 HTML
    container.innerHTML = articles.map(art => `
        <div class="card" data-url="${art.url || '#'}">
            <h4>${art.title}</h4>
            <p style="color: #888; font-size: 12px; margin-bottom:0;">来源：${art.source} 🔗</p>
        </div>
    `).join('');

    // 为生成的卡片绑定统一的点击跳转逻辑
    const cards = container.querySelectorAll('.card');
    cards.forEach(card => {
        card.addEventListener('click', function() {
            const url = this.getAttribute('data-url');
            if (url && url !== '#') {
                window.open(url, '_blank'); // 开辟新标签页安全跳转
            } else {
                alert('该科普卡片暂无有效跳转链接');
            }
        });
    });
}


// ==================== 6. 绑定图谱点击事件 ====================
myChart.on('click', function (params) {
    if (params.dataType === 'node') {
        const name = params.name;
        updateDetails(name);         
        updateRecommendations(name); 
    }
});

// 初始化页面默认展示数据
updateRecommendations('default');

// 窗口自适应
window.addEventListener('resize', () => myChart.resize());