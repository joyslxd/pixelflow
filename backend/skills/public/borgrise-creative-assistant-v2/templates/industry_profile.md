# 垂类 Skill 行业规范（开发版）

> Skill 名称：`industry_profile`  
> 说明：根据产品品类，输出该行业的产品创作画像（product_creative_profile），供后续创意方向、plan.md、视频生成复用  
> 本期覆盖 **6大行业**：美妆护肤、食品饮料、服饰鞋包、3C数码、家清日用、宠物用品  
> 交付对象：后端开发（LLM Prompt + 规则引擎）

---

## 一、整体架构

```
用户填写表单 → 识别品类 → 调用对应垂类Skill → 输出 product_creative_profile
                                                                ↓
                                        ┌───────────────────────┼───────────────────────┐
                                        ↓                       ↓                       ↓
                                  创意方向生成              plan.md撰写              视频生成
                              （directions Prompt）      （plan Prompt）          （frame_description）
```

### 垂类Skill 的定位

- **不是最终Prompt**，而是行业约束和创作规范的「配置文件」
- 所有行业统一输出为 `product_creative_profile` 结构
- 后续各环节的Prompt中，通过 `{{industry_profile}}` 变量注入行业约束

---

## 二、统一输出格式：product_creative_profile

```json
{
  "profile_version": "1.0",
  "industry": "行业编码",
  "industry_name": "行业中文名",
  "product_creative_profile": {
    "core_expression_rules": {
      "must_include": ["表达维度1", "表达维度2"],
      "must_avoid": ["禁用表达1", "禁用表达2"],
      "description": "该行业产品核心表达规则的详细说明"
    },
    "key_scenes": {
      "before_after": {
        "applicable": true,
        "description": "使用前后对比场景的描述",
        "example_frames": ["使用前XXX...使用后XXX..."]
      },
      "usage_moment": {
        "description": "使用时刻场景",
        "example_frames": ["场景1描述", "场景2描述"]
      },
      "atmosphere": {
        "description": "场景氛围要求",
        "lighting": "光影要求",
        "color_tone": "色调要求"
      }
    },
    "product_display_rules": {
      "packaging": {
        "must_show": true,
        "description": "包装展示要求"
      },
      "texture_detail": {
        "must_show": true,
        "description": "质感/细节展示要求",
        "close_up_parts": ["需要特写的部位"]
      },
      "scale_reference": {
        "description": "尺寸/比例参照物"
      }
    },
    "safety_compliance": {
      "disclaimers": ["必须出现的免责声明"],
      "forbidden_claims": ["禁止的功效/效果承诺"],
      "compliance_notes": "合规注意事项"
    },
    "audience_pain_points": [
      {
        "pain": "痛点描述",
        "scenario": "使用场景",
        "creative_hook": "创意切入点"
      }
    ],
    "emotional_triggers": [
      {
        "emotion": "情绪名称",
        "trigger": "触发方式",
        "creative_angle": "创意角度"
      }
    ],
    "visual_anchor_keywords": ["视觉锚点关键词1", "视觉锚点关键词2"],
    "prompt_injection": {
      "creative_direction_note": "注入创意方向生成Prompt的行业约束段",
      "plan_note": "注入plan.md的行业场景补充",
      "video_generation_note": "注入视频生成frame_description的行业约束段"
    }
  }
}
```

---

## 三、6大行业垂类Skill规范

### 3.1 美妆护肤（beauty）

```json
{
  "profile_version": "1.0",
  "industry": "beauty",
  "industry_name": "美妆护肤",
  "product_creative_profile": {
    "core_expression_rules": {
      "must_include": ["肤感描述", "质地展示", "使用手法"],
      "must_avoid": ["医疗功效承诺", "绝对化用语（100%有效/立即见效）", "药物类比"],
      "description": "美妆护肤产品必须围绕'感官体验'和'效果可视化'展开。肤感（清爽/滋润/不粘腻）、质地（水润/绵密/轻薄）、使用手法（按压/涂抹/轻拍）是三大核心表达维度。功效描述必须留有合理预期空间，不得承诺具体医疗效果。"
    },
    "key_scenes": {
      "before_after": {
        "applicable": true,
        "description": "使用前后面部/皮肤状态对比是核心转化场景，但必须标注'效果因人而异'",
        "example_frames": [
          "使用前：素颜状态下的皮肤细节（毛孔/暗沉/干燥）",
          "使用中：产品在皮肤上的涂抹过程，质地变化",
          "使用后：皮肤状态的改善呈现（光泽度/水润度提升）"
        ]
      },
      "usage_moment": {
        "description": "护肤/化妆仪式感的时刻",
        "example_frames": [
          "晨间护肤routine的第一步，阳光透过窗帘",
          "睡前卸妆后的放松时刻，柔和暖光",
          "化妆前的底妆准备，对镜子的专注神情",
          "紧急出门前的快速补妆，争分夺秒"
        ]
      },
      "atmosphere": {
        "description": "干净、明亮、高级感",
        "lighting": "柔和自然光或专业环形补光灯，避免阴影",
        "color_tone": "低饱和度、粉白调、清新通透感"
      }
    },
    "product_display_rules": {
      "packaging": {
        "must_show": true,
        "description": "包装外观必须至少出现1次，展示品牌辨识度。瓶身/管身的设计细节要清晰"
      },
      "texture_detail": {
        "must_show": true,
        "description": "质地特写是转化关键，必须展示产品本体形态",
        "close_up_parts": ["膏体延展性", "液体流动性", "泡沫绵密度", "上脸后的肤感状态"]
      },
      "scale_reference": {
        "description": "可用手指、手背、脸部轮廓作为尺寸参照"
      }
    },
    "safety_compliance": {
      "disclaimers": ["效果因人而异", "个人肤质不同，使用感受可能有所差异"],
      "forbidden_claims": ["治愈", "根治", "医疗效果", "100%有效", "立即见效", "替代药物", "修复敏感肌（除非有特证）"],
      "compliance_notes": "不得宣称医疗功效，不得使用绝对化用语，特殊化妆品需标注批准文号"
    },
    "audience_pain_points": [
      {
        "pain": "皮肤干燥起皮，上妆卡粉",
        "scenario": "早上化妆时粉底推不开",
        "creative_hook": "展示涂抹前后的卡粉对比，强调急救补水"
      },
      {
        "pain": "毛孔粗大，皮肤暗沉",
        "scenario": "照镜子时对皮肤状态不满意",
        "creative_hook": "近距离展示毛孔细节，再展示改善效果"
      },
      {
        "pain": "敏感肌不敢乱用护肤品",
        "scenario": "换季时皮肤泛红刺痛",
        "creative_hook": "温和测试（如PH试纸/花瓣实验），建立安全感"
      },
      {
        "pain": "产品太多不知道选哪个",
        "scenario": "面对满桌护肤品无从选择",
        "creative_hook": "精简护肤概念，一瓶多效解决"
      }
    ],
    "emotional_triggers": [
      {
        "emotion": "自我宠爱",
        "trigger": "护肤=对自己的投资",
        "creative_angle": "你值得更好的"
      },
      {
        "emotion": "安心感",
        "trigger": "成分安全+效果可见",
        "creative_angle": "看得见的温和，用得放心"
      },
      {
        "emotion": "期待感",
        "trigger": "28天周期/坚持使用的效果",
        "creative_angle": "给肌肤一点时间，它会回报你"
      }
    ],
    "visual_anchor_keywords": ["水滴", "光泽肌", "绵密泡沫", "丝滑延展", "清透", "裸妆感", "镜子前的自己"],
    "prompt_injection": {
      "creative_direction_note": "创意必须围绕'感官体验'展开，强调肤感、质地、使用手法的可视化。避免功效承诺，聚焦使用体验的美好感受。",
      "plan_note": "剧情中必须设计至少1个'使用过程展示'场景（涂抹/拍打/按摩），1个'质地特写'场景。产品露出要自然融入护肤routine中。",
      "video_generation_note": "For beauty products: Always include close-up shots of texture and application on skin. Lighting should be soft and flattering, emphasizing skin glow and product texture. Show the product packaging clearly at least once."
    }
  }
}
```

---

### 3.2 食品饮料（food）

```json
{
  "profile_version": "1.0",
  "industry": "food",
  "industry_name": "食品饮料",
  "product_creative_profile": {
    "core_expression_rules": {
      "must_include": ["口味描述", "食用场景", "质感/口感展示"],
      "must_avoid": ["夸大健康功效（治愈/治疗）", "绝对化口感承诺", "与药品类比"],
      "description": "食品饮料必须围绕'感官食欲'和'场景代入'展开。口味（酸甜/浓郁/清爽/层次丰富）、食用场景（早餐/加班/聚会/独食）、质感（拉丝/爆浆/酥脆/绵密）是三大核心表达维度。安全表述是底线，不得暗示医疗功效。"
    },
    "key_scenes": {
      "before_after": {
        "applicable": true,
        "description": "打开包装→第一口反应→吃完满足感，是完整的食欲曲线",
        "example_frames": [
          "打开包装/瓶盖的瞬间，香气溢出",
          "第一口的表情反应（眼睛亮起来）",
          "咀嚼/吞咽过程的享受表情",
          "吃完后的满足感和回味"
        ]
      },
      "usage_moment": {
        "description": "吃的场景决定购买理由",
        "example_frames": [
          "早起匆忙的早餐桌，抓起来就走",
          "下午3点工位上的能量补给",
          "深夜追剧时的零食搭子",
          "运动后的大口畅饮",
          "朋友聚会时的分享时刻"
        ]
      },
      "atmosphere": {
        "description": "温暖、诱人、有食欲",
        "lighting": "暖色调侧光或顶光，突出食物质感和光泽",
        "color_tone": "暖色调（橙黄/焦糖色），高饱和度，让人有食欲"
      }
    },
    "product_display_rules": {
      "packaging": {
        "must_show": true,
        "description": "包装正面至少出现1次，展示品牌和产品名"
      },
      "texture_detail": {
        "must_show": true,
        "description": "食物质感/口感的特写是核心转化点",
        "close_up_parts": ["切面纹理", "拉丝/流心效果", "气泡/碳酸感", "汁水溢出", "酥脆掉渣"]
      },
      "scale_reference": {
        "description": "可用手、嘴、餐具作为尺寸参照，展示真实大小"
      }
    },
    "safety_compliance": {
      "disclaimers": ["适量食用", "个人口味偏好不同"],
      "forbidden_claims": ["治愈", "治疗", "替代药物", "100%天然（无证明）", "零风险", "绝对安全"],
      "compliance_notes": "不得宣称保健/治疗功能，不得误导消费者关于营养成分的表述，特殊人群（孕妇/儿童）需标注适用性"
    },
    "audience_pain_points": [
      {
        "pain": "早上没时间吃早餐",
        "scenario": "赶地铁前随便塞一口",
        "creative_hook": "30秒搞定营养早餐"
      },
      {
        "pain": "下午犯困没精神",
        "scenario": "下午3点趴在工位上",
        "creative_hook": "一口提神，效率翻倍"
      },
      {
        "pain": "想吃点好的又担心不健康",
        "scenario": "拿起零食看配料表犹豫",
        "creative_hook": "配料表干净到可以放心吃"
      },
      {
        "pain": "一个人吃饭没胃口",
        "scenario": "独自在家不知道吃什么",
        "creative_hook": "一个人也要好好吃饭"
      }
    ],
    "emotional_triggers": [
      {
        "emotion": "幸福感",
        "trigger": "好吃的带来的即时满足",
        "creative_angle": "一口治愈坏心情"
      },
      {
        "emotion": "归属感",
        "trigger": "熟悉的味道=家的感觉",
        "creative_angle": "小时候的味道"
      },
      {
        "emotion": "分享欲",
        "trigger": "好吃到想安利给所有人",
        "creative_angle": "好吃到办公室人手一份"
      }
    ],
    "visual_anchor_keywords": ["拉丝", "流心", "爆浆", "酥脆", "汁水", "热气腾腾", "咀嚼", "满足表情", "第一口"],
    "prompt_injection": {
      "creative_direction_note": "创意必须围绕'食欲激发'和'场景代入'展开。口味描述要有画面感（不是'好吃'，而是'一口咬下去，浓郁的芝士在口腔里化开'）。食用场景要具体真实。",
      "plan_note": "剧情中必须设计1个'开箱/开袋瞬间'（香气溢出），1个'第一口反应'（表情特写），1个'食用过程'（咀嚼/吞咽的享受感）。产品露出在吃的动作中自然出现。",
      "video_generation_note": "For food/beverage: Always include close-up of the first bite/drink reaction. Lighting should be warm and appetizing. Show steam/vapor for hot food, condensation for cold drinks. Focus on texture details like cheese pull, juice flow, or crisp layers."
    }
  }
}
```

---

### 3.3 服饰鞋包（clothing）

```json
{
  "profile_version": "1.0",
  "industry": "clothing",
  "industry_name": "服饰鞋包",
  "product_creative_profile": {
    "core_expression_rules": {
      "must_include": ["版型展示", "材质细节", "穿搭场景氛围"],
      "must_avoid": ["绝对化身材承诺（显瘦10斤/瞬间变高）", "贬低用户现有穿着", "过度P图误导"],
      "description": "服饰鞋包必须围绕'上身效果'和'场景穿搭'展开。版型（修身/宽松/直筒/oversize）、材质（柔软/挺括/透气/垂坠感）、穿搭场景氛围（通勤/约会/运动/度假）是三大核心表达维度。展示真实穿着效果，不得过度修饰。"
    },
    "key_scenes": {
      "before_after": {
        "applicable": true,
        "description": "穿前vs穿后的形象对比是核心转化场景",
        "example_frames": [
          "打开衣柜的纠结（没衣服穿）",
          "换上新衣服后的整体效果展示",
          "走动/转身的动态展示（版型效果）",
          "细节特写（面料/走线/五金件）"
        ]
      },
      "usage_moment": {
        "description": "穿搭场景=购买理由",
        "example_frames": [
          "周一早高峰的地铁通勤",
          "周五下班后的约会赴约",
          "周末的户外运动/登山",
          "机场赶飞机的舒适穿搭",
          "和闺蜜的下午茶拍照局"
        ]
      },
      "atmosphere": {
        "description": "有风格、有态度、有场景代入感",
        "lighting": "自然光为主，户外拍摄用黄金时刻光线",
        "color_tone": "根据风格定调性，通勤偏冷/度假偏暖"
      }
    },
    "product_display_rules": {
      "packaging": {
        "must_show": false,
        "description": "服饰鞋包重点展示穿着效果，包装非必要"
      },
      "texture_detail": {
        "must_show": true,
        "description": "面料质感、走线工艺、五金件等细节是品质证明",
        "close_up_parts": ["面料纹理", "走线细节", "拉链/纽扣五金", "鞋底纹路", "包包内里"]
      },
      "scale_reference": {
        "description": "真人上身展示是最核心的比例参照"
      }
    },
    "safety_compliance": {
      "disclaimers": ["穿着效果因个人体型而异", "颜色因显示设备可能略有差异"],
      "forbidden_claims": ["显瘦10斤", "穿上立即变高", "100%还原图片效果", "适合所有身材"],
      "compliance_notes": "不得承诺具体身材改变效果，模特图与普通人效果差异需合理呈现，不得使用过度修图误导"
    },
    "audience_pain_points": [
      {
        "pain": "早上不知道穿什么",
        "scenario": "站在衣柜前发呆10分钟",
        "creative_hook": "一套搞定，不再纠结"
      },
      {
        "pain": "网购衣服质量差",
        "scenario": "收到实物与图片差距大",
        "creative_hook": "细节实拍，所见即所得"
      },
      {
        "pain": "穿出去撞衫尴尬",
        "scenario": "逛街发现和路人同款",
        "creative_hook": "小众设计感，不撞款"
      },
      {
        "pain": "好看但不舒服",
        "scenario": "穿了一整天勒得慌",
        "creative_hook": "好看和舒服，这次不用选"
      }
    ],
    "emotional_triggers": [
      {
        "emotion": "自信感",
        "trigger": "穿得好看=心情好=自信",
        "creative_angle": "今天的你，值得被看见"
      },
      {
        "emotion": "认同感",
        "trigger": "穿对风格，找到同好",
        "creative_angle": "懂你的人，一眼就看出来了"
      },
      {
        "emotion": "新鲜感",
        "trigger": "新造型=新开始",
        "creative_angle": "换个风格，换种心情"
      }
    ],
    "visual_anchor_keywords": ["上身效果", "走动展示", "面料特写", "搭配全景", "细节走线", "转身展示", "场景穿搭"],
    "prompt_injection": {
      "creative_direction_note": "创意必须围绕'穿搭场景'和'上身效果'展开。避免单纯展示衣服平铺，必须真人上身+场景代入。版型描述要具体（不是'好看'，而是'直筒版型藏肉不拖沓'）。材质描述要有触感（'摸起来像云朵一样软'）。",
      "plan_note": "剧情中必须设计1个'穿搭过程'（从选衣服到穿好的过程），1个'全身展示'（走动/转身展示版型），1个'细节特写'（面料/走线/五金）。场景要具体（通勤/约会/运动）。",
      "video_generation_note": "For clothing/shoes/bags: Always include full-body shots with the model walking or turning. Show the fit and silhouette in motion. Include close-ups of fabric texture and stitching details. Use natural lighting and real-world settings (street, office, cafe) for lifestyle context."
    }
  }
}
```

---

### 3.4 3C数码（digital）

```json
{
  "profile_version": "1.0",
  "industry": "digital",
  "industry_name": "3C数码",
  "product_creative_profile": {
    "core_expression_rules": {
      "must_include": ["核心参数（关键指标）", "功能演示", "使用场景"],
      "must_avoid": ["虚假参数对比", "绝对性能承诺（永不卡顿/无限续航）", "贬低竞品（点名道姓）"],
      "description": "3C数码必须围绕'功能价值'和'场景体验'展开。核心参数（像素/续航/速度/屏幕等关键指标）、功能演示（具体操作过程）、使用场景（工作/娱乐/创作/出行）是三大核心表达维度。可以做对比，但避免虚假承诺和贬低具体竞品。"
    },
    "key_scenes": {
      "before_after": {
        "applicable": true,
        "description": "用旧设备的问题 → 换新设备后的顺畅体验",
        "example_frames": [
          "旧手机的痛点场景（卡顿/没电/拍照差）",
          "新设备解决痛点的过程",
          "使用新功能的具体操作演示",
          "效率提升/体验升级的结果展示"
        ]
      },
      "usage_moment": {
        "description": "数码产品的使用场景=购买理由",
        "example_frames": [
          "多任务处理的办公场景（分屏/切换）",
          "游戏场景的高帧率流畅画面",
          "外出拍摄的创作场景",
          "差旅路上的轻便办公",
          "学生党的网课/笔记场景"
        ]
      },
      "atmosphere": {
        "description": "科技感+专业感+未来感",
        "lighting": "冷色调环境光+产品屏幕自发光",
        "color_tone": "冷色调（蓝/银/黑），高对比度，突出科技感"
      }
    },
    "product_display_rules": {
      "packaging": {
        "must_show": true,
        "description": "数码产品开箱是仪式感，包装展示增加高端感"
      },
      "texture_detail": {
        "must_show": true,
        "description": "产品工艺细节体现品质",
        "close_up_parts": ["摄像头模组", "屏幕边缘工艺", "金属边框质感", "接口细节", "背面纹理"]
      },
      "scale_reference": {
        "description": "手持展示是最核心的尺寸参照"
      }
    },
    "safety_compliance": {
      "disclaimers": ["实际体验因使用环境而异", "数据来自实验室测试条件"],
      "forbidden_claims": ["永不卡顿", "无限续航", "比其他品牌都好", "100%无故障", "治愈/治疗（针对健康类数码）"],
      "compliance_notes": "参数对比需有据可依，避免绝对化性能承诺，不得点名贬低竞品，辐射/安全类参数需符合国家标准"
    },
    "audience_pain_points": [
      {
        "pain": "手机卡顿，打开APP要转圈",
        "scenario": "赶时间的时候手机卡死",
        "creative_hook": "顺滑到忘记等待"
      },
      {
        "pain": "出门半天就没电",
        "scenario": "到处找充电宝",
        "creative_hook": "一天一充，安心出门"
      },
      {
        "pain": "拍照不好看，发不了朋友圈",
        "scenario": "聚会拍的照片都糊了",
        "creative_hook": "随手一拍就是大片"
      },
      {
        "pain": "功能太多用不上，操作复杂",
        "scenario": "买了新功能从没打开过",
        "creative_hook": "好用的功能，你会每天都想打开"
      }
    ],
    "emotional_triggers": [
      {
        "emotion": "掌控感",
        "trigger": "设备流畅=工作生活尽在掌控",
        "creative_angle": "高效的人，从不被设备拖后腿"
      },
      {
        "emotion": "成就感",
        "trigger": "用好设备做出好作品",
        "creative_angle": "你的创作，值得更好的工具"
      },
      {
        "emotion": "科技感",
        "trigger": "前沿科技带来的新奇体验",
        "creative_angle": "未来已来，触手可及"
      }
    ],
    "visual_anchor_keywords": ["手持展示", "屏幕亮起", "流畅操作", "开箱瞬间", "产品特写", "场景使用", "参数界面"],
    "prompt_injection": {
      "creative_direction_note": "创意必须围绕'功能演示'和'场景体验'展开。避免纯参数罗列，要把参数翻译成用户能感知的体验（不是'5000mAh'，而是'从早用到晚，还剩30%'）。可以做使用前后对比，但必须真实不夸张。",
      "plan_note": "剧情中必须设计1个'痛点场景'（旧设备的问题），1个'功能演示'（具体操作+效果展示），1个'使用场景'（真实环境中的使用）。产品露出在前后的对比中自然出现。",
      "video_generation_note": "For 3C digital products: Always include hands-on demonstration of key features. Show the product in real usage scenarios (office desk, outdoor, commute). Include close-ups of screen quality, camera module, and build details. Use cool-toned lighting to emphasize tech aesthetic."
    }
  }
}
```

---

### 3.5 家清日用（home_cleaning）

```json
{
  "profile_version": "1.0",
  "industry": "home_cleaning",
  "industry_name": "家清日用",
  "product_creative_profile": {
    "core_expression_rules": {
      "must_include": ["使用痛点", "清洁前后对比", "家庭场景氛围"],
      "must_avoid": ["夸大清洁效果（100%除菌/瞬间变白）", "过度渲染脏乱", "使用场景误导"],
      "description": "家清日用必须围绕'问题解决'和'效果可视化'展开。使用痛点（家务繁琐/清洁困难/气味问题）、清洁前后对比（效果最直接）、家庭场景氛围（温馨整洁的家）是三大核心表达维度。效果展示要真实，不得夸大。"
    },
    "key_scenes": {
      "before_after": {
        "applicable": true,
        "description": "清洁前后对比是家清产品最核心的转化场景，必须设计",
        "example_frames": [
          "使用前：脏乱的场景（油渍/水垢/灰尘/异味源）",
          "使用过程中：喷洒/擦拭/清洁的动作过程",
          "使用后：干净整洁的场景，细节特写",
          "嗅觉暗示：清新气味的视觉表达（可用心形/星星动画暗示）"
        ]
      },
      "usage_moment": {
        "description": "清洁场景=使用场景",
        "example_frames": [
          "周末大扫除时的厨房清洁",
          "饭后快速清理餐桌",
          "洗澡后清除浴室水垢",
          "换季整理衣柜的除螨除味",
          "客人来之前的紧急整理"
        ]
      },
      "atmosphere": {
        "description": "干净、明亮、温馨、整洁",
        "lighting": "明亮均匀的室内光，突出干净感",
        "color_tone": "白色调为主，搭配淡蓝/淡绿等清新色调"
      }
    },
    "product_display_rules": {
      "packaging": {
        "must_show": true,
        "description": "包装外观+喷头/泵头等使用部件要清晰展示"
      },
      "texture_detail": {
        "must_show": true,
        "description": "泡沫质地、液体流动性、清洁过程的物理变化",
        "close_up_parts": ["泡沫绵密度", "液体喷洒状态", "擦拭过程", "清洁前后的材质表面对比"]
      },
      "scale_reference": {
        "description": "用熟悉的家庭物品作为尺寸参照（手掌、厨房台面、马桶圈等）"
      }
    },
    "safety_compliance": {
      "disclaimers": ["使用效果因污渍程度而异", "请按说明使用", "避免接触眼睛"],
      "forbidden_claims": ["100%除菌", "完全无菌", "医疗级消毒", "瞬间变白", "无需擦洗"],
      "compliance_notes": "除菌/消毒类宣称需有检测报告支撑，不得暗示医疗消毒级别，成分说明需真实准确，需标注使用注意事项"
    },
    "audience_pain_points": [
      {
        "pain": "厨房油污难清理",
        "scenario": "炒完菜灶台一层油",
        "creative_hook": "喷一喷，油渍自己往下流"
      },
      {
        "pain": "家里总有异味",
        "scenario": "卫生间/厨房/衣柜有味道",
        "creative_hook": "不是遮盖，是真的分解异味"
      },
      {
        "pain": "清洁费时费力",
        "scenario": "周末大半天都在做家务",
        "creative_hook": "10分钟搞定，剩下的时间留给自己"
      },
      {
        "pain": "清洁剂刺鼻伤手",
        "scenario": "每次用完手都干干的",
        "creative_hook": "温和到不用戴手套"
      }
    ],
    "emotional_triggers": [
      {
        "emotion": "成就感",
        "trigger": "打扫干净后的满足感",
        "creative_angle": "干净的家，是最好的治愈"
      },
      {
        "emotion": "轻松感",
        "trigger": "家务变简单，不再负担",
        "creative_angle": "家务这件事，可以更轻松的"
      },
      {
        "emotion": "安心感",
        "trigger": "家里有宝宝/宠物，需要安全清洁",
        "creative_angle": "家人的安全，从选择开始"
      }
    ],
    "visual_anchor_keywords": ["清洁前后对比", "泡沫特写", "喷洒动作", "干净表面反光", "整洁房间全景", "轻松擦拭"],
    "prompt_injection": {
      "creative_direction_note": "创意必须围绕'问题解决'和'效果可视化'展开。痛点场景要真实（不是演出来的假脏），清洁过程要有爽感（污渍消失的瞬间），清洁后要有成就感的情绪表达。避免恐怖式营销（过度放大脏东西）。",
      "plan_note": "剧情中必须设计1个'脏污场景'（真实的使用痛点），1个'清洁过程'（展示产品使用方法和效果），1个'清洁后场景'（整洁干净的结果）。产品露出在清洁动作中自然出现。",
      "video_generation_note": "For home cleaning products: Always include before/after cleaning shots. Show the cleaning process with satisfying foam/spray action. Lighting should be bright and clean, emphasizing the spotless result. Use macro shots for texture details like foam density and surface cleanliness."
    }
  }
}
```

---

### 3.6 宠物用品（pet）

```json
{
  "profile_version": "1.0",
  "industry": "pet",
  "industry_name": "宠物用品",
  "product_creative_profile": {
    "core_expression_rules": {
      "must_include": ["宠物体型特征", "使用动作展示", "安全感呈现", "主人视角"],
      "must_avoid": ["恐吓式营销（不用就会生病）", "过度拟人化", "宠物痛苦画面"],
      "description": "宠物用品必须围绕'宠物真实反应'和'主人情感连接'展开。宠物体型特征（适合什么品种/体型）、使用动作展示（宠物如何自然使用）、安全感呈现（材质安全/设计安全）、主人视角（看着宠物开心自己也开心）是四大核心表达维度。宠物必须是开心自然的状态。"
    },
    "key_scenes": {
      "before_after": {
        "applicable": true,
        "description": "使用前后的宠物状态对比",
        "example_frames": [
          "使用前：宠物的痛点状态（焦躁/挑食/毛打结/无聊）",
          "使用过程：宠物自然接受产品的过程",
          "使用后：宠物的开心满足状态",
          "主人反应：看到宠物开心的欣慰表情"
        ]
      },
      "usage_moment": {
        "description": "宠物+主人的互动时刻",
        "example_frames": [
          "喂食时刻（日常互动最频繁）",
          "玩耍互动时刻",
          "洗澡护理时刻",
          "外出遛弯时刻",
          "宠物独自在家的场景",
          "睡前安抚时刻"
        ]
      },
      "atmosphere": {
        "description": "温暖、治愈、人宠和谐",
        "lighting": "柔和自然光，家居温馨感",
        "color_tone": "暖色调，柔和肤色，家的感觉"
      }
    },
    "product_display_rules": {
      "packaging": {
        "must_show": true,
        "description": "包装展示增加信任感（成分/品牌/适用对象）"
      },
      "texture_detail": {
        "must_show": true,
        "description": "材质安全性是宠物主人的核心关注点",
        "close_up_parts": ["产品材质纹理", "食盆内壁", "玩具咬合面", "窝垫填充物", "项圈/牵引绳接口"]
      },
      "scale_reference": {
        "description": "宠物本身是最好的尺寸参照"
      }
    },
    "safety_compliance": {
      "disclaimers": ["请根据宠物体型选择合适尺寸", "使用初期请观察宠物适应情况"],
      "forbidden_claims": ["100%安全", "所有宠物都适用", "替代医疗", "治愈疾病", "不用就会生病"],
      "compliance_notes": "不得暗示医疗功效，食品类需标注成分和适用宠物类型，玩具类需标注材质安全等级，不得使用痛苦/恐惧画面"
    },
    "audience_pain_points": [
      {
        "pain": "宠物挑食不爱吃饭",
        "scenario": "每天换着花样做宠物都不吃",
        "creative_hook": "打开盖子就冲过来的那种香"
      },
      {
        "pain": "担心宠物在家无聊",
        "scenario": "上班时宠物一个人在家",
        "creative_hook": "它在家也有人陪"
      },
      {
        "pain": "宠物用品材质不放心",
        "scenario": "新闻说某品牌有毒",
        "creative_hook": "材质透明到你自己都想用"
      },
      {
        "pain": "宠物不爱洗澡/护理",
        "scenario": "每次洗澡像打仗",
        "creative_hook": "它居然自己凑过来了"
      }
    ],
    "emotional_triggers": [
      {
        "emotion": "治愈感",
        "trigger": "宠物可爱的样子+主人温柔的眼神",
        "creative_angle": "它好，你就好"
      },
      {
        "emotion": "责任感",
        "trigger": "给宠物最好的=做一个好主人",
        "creative_angle": "爱它，就给它用最好的"
      },
      {
        "emotion": "陪伴感",
        "trigger": "宠物是家人，不是动物",
        "creative_angle": "家人值得被好好对待"
      }
    ],
    "visual_anchor_keywords": ["宠物开心表情", "人宠互动", "自然使用动作", "材质特写", "温暖家居环境", "主人宠溺眼神"],
    "prompt_injection": {
      "creative_direction_note": "创意必须围绕'人宠情感连接'展开。宠物必须是自然开心的状态，不能用强迫/摆拍。主人视角是关键（看着宠物开心的满足感）。安全感的建立是转化的核心。",
      "plan_note": "剧情中必须设计1个'宠物自然使用产品的过程'（不是摆拍，是宠物真的在享受），1个'人宠互动场景'（主人和宠物的温馨时刻），1个'主人欣慰表情'（看到宠物开心的满足感）。",
      "video_generation_note": "For pet products: Always include the pet's natural, happy reaction. Show the pet voluntarily using/enjoying the product. Include owner-pet interaction moments with warm eye contact. Use soft natural lighting in home settings. Never show forced or uncomfortable pet behavior."
    }
  }
}
```

---

## 四、开发接入指南

### 4.1 接口定义

```
POST /api/v1/skill/industry/profile
Content-Type: application/json

Request:
{
  "product_category": "beauty",
  "product_name": "某品牌面霜",
  "target_audience": "gen_z"
}

Response:
{
  "skill": "industry_profile",
  "profile": { ...product_creative_profile JSON... }
}
```

### 4.2 调用时机

```
用户确认产品信息（表单提交后）
    ↓
并行调用：
    ├── 营销选题Skill → 产品诊断
    └── 垂类Skill → product_creative_profile
    ↓
两个结果合并，传给故事策划Skill
```

### 4.3 在后续Prompt中的注入方式

```
创意方向Prompt:
  {{product_info}}
  {{diagnosis_report}}
  {{industry_profile.prompt_injection.creative_direction_note}}  ← 注入行业约束
  {{selected_trend}}

plan.md Prompt:
  {{product_info}}
  {{diagnosis_report}}
  {{plan_summary}}
  {{industry_profile.prompt_injection.plan_note}}  ← 注入行业场景

视频生成 frame_description:
  {{scene_description}}
  {{industry_profile.prompt_injection.video_generation_note}}  ← 注入行业视觉约束
```

---
