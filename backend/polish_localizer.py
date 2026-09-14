# -*- coding: utf-8 -*-
"""
polish_localizer.py — 本地游戏文本润色工具（无外部依赖）
输入: 翻译好的 xlsx (sheet「本土化」, A列=id, B列=中文)
      原始 CSV (chunks/updatefs_localization_NN.csv)
处理: 1. 按 id 对齐
      2. 术语纠正表 TERM (越语残留专名/错译 -> 中文)
      3. 占位符从原文还原 (♥♣♦♠★☆●■▲◆※◎◇□△▽◁▷◈)
      4. 修正 Google 已知错译 FIX
      5. 缺失条目补译 (FALLBACK)
输出: translated/updatefs_localization_NN.json (34 个) + 质检报告
"""
import csv, json, os, re, sys

XLSX = r"D:\App\Frida Android\cocos-pak-localization-studio-v3a-hotfix2\pak\updatefs\text_csv_api\updatefs\localization_master_zh.xlsx"
SHEET = "本土化"
CHUNKS = "chunks"
OUT = "translated"
XLSX_OUT = "localization_master_zh_polished.xlsx"   # 润色后的 xlsx 输出

SYMS = "♥♣♦♠★☆●■▲◆※◎◇□△▽◁▷◈"

# ---------------- 术语纠正表：越语残留专名/游戏术语 -> 中文 ----------------
TERM = {
    # 复合专名（长词优先匹配）
    "Tinh Quân": "星君", "Huyền Thiết Giáp": "玄铁甲", "Huyền Thiết": "玄铁",
    "Chấn Lôi Khải": "震雷铠", "Hỗn Nguyên Giáp": "混元甲", "Vô Cực Quy Chân Kính": "无极归真镜",
    "Quy Chân Kính": "归真镜", "Thái Cực": "太极", "Trấn Hồn Thạch": "镇魂石", "Trấn Hồn": "镇魂",
    "Thôi Phong Lệnh": "摧风令", "Thôi Phong": "摧风", "Bá Lạc Nhãn": "伯乐眼", "Bá Lạc kính": "伯乐镜",
    "Bá Lạc Kính": "伯乐镜", "Bá Lạc": "伯乐", "Hồ Lô": "葫芦", "Hồn Phách Hồ Lô": "魂魄葫芦",
    "Phong Ma Bích": "封魔璧", "Càn Khôn Luân": "乾坤轮", "Càn Khôn": "乾坤",
    "Bát Quái": "八卦", "Bát Quái kính": "八卦镜", "Thiên Cơ Đồng": "天机筒", "Chuyển Long Xu": "转龙枢",
    "Hoàn Quan nhãn": "环观眼", "Hoàn Quan": "环观", "Đại địa nhãn": "大地眼", "Đại Địa nhãn": "大地眼",
    "Bạo Phong nhãn": "暴风眼", "Liệt Diệm nhãn": "烈焰眼", "Liệt Diệm": "烈焰", "Phong Bạo nhãn": "风暴眼",
    "Hoàng thủy tinh": "黄水晶", "Hoàng Thuỷ tinh": "黄水晶", "Hoàng Thủy Tinh": "黄水晶",
    "Lục thủy tinh": "绿水晶", "Lục Thuỷ tinh": "绿水晶", "Lục Thủy Tinh": "绿水晶",
    "Tử thủy tinh": "紫水晶", "Tử Thủy Tinh": "紫水晶", "Hồng Thủy tinh": "红水晶",
    "Lam thủy tinh": "蓝水晶", "Lam Thủy Tinh": "蓝水晶", "Hồng Bảo Thạch": "红宝石", "Hồng Bảo thạch": "红宝石",
    "Lam Bảo Thạch": "蓝宝石", "Lam Bảo thạch": "蓝宝石", "Hoàng Bảo thạch": "黄宝石", "Hoàng Bảo Thạch": "黄宝石",
    "Lục Bảo thạch": "绿宝石", "Tử Bảo Thạch": "紫宝石", "Hoàng Bích Tỷ": "黄碧玺", "Tử Bích Tỉ": "紫碧玺",
    "Lục Bích Tỷ": "绿碧玺", "Tha Sơn Thạch": "他山石", "Tha Sơn thạch": "他山石", "Sơn Thạch": "山石",
    "Tha Sơ Thạch": "他山石", "Dung Tinh Lộ": "熔晶露", "Tứ Tượng": "四象", "Hỗn Nguyên Châu": "混元珠",
    "Hỗn Nguyên": "混元", "Tướng Quân Lệnh": "将军令", "Tướng Quân lệnh": "将军令", "Tướng Quân": "将军",
    "Khoa Nga Linh Châu": "蚪蛾灵珠", "Khoa Nga": "蚪蛾", "Cửu Tinh Tứ Đàn Châu": "九星四坛珠",
    "Thạch Dương Giác": "石羊角", "Định Âm Châu": "定音珠", "Khai Thiên kính": "开天镜", "Tẩy Tủy kinh": "洗髓经",
    "Tẩy Tủy đơn": "洗髓丹", "Tẩy Tủy": "洗髓", "Đan Phôi": "丹胚", "Thuận Mạch đơn": "顺脉丹",
    "Cửu Chuyển Tiên Đan": "九转仙丹", "Cửu Chuyển": "九转", "Ngọc Hồng Thảo": "玉红草", "U Minh Thảo": "幽冥草",
    "Thủ Dương Sơn": "首阳山", "Tuyết Cốt": "雪骨", "Chỉ Huyết Lộ": "止血露", "Tiểu Hồng đơn": "小红丹",
    "Tiểu Hồng Đơn": "小红丹", "Thủy Linh Lung": "水玲珑", "Băng Hỏa Khí Tức": "冰火气息",
    "Hồn Phách Hồ Lô": "魂魄葫芦", "Băng Hỏa Ma": "冰火魔", "Tinh Tượng đồ": "星象图", "Quan Tinh Giản": "观星简",
    "Lục Hồn Phiên": "六魂幡", "Thổ Linh Phù": "土灵符", "Thưởng Kim bài": "赏金牌", "Thưởng Kim Bài": "赏金牌",
    "Khao quân lệnh": "犒军令", "Hoàn Danh Chú": "还名符", "Mai danh ẩn tính": "隐姓埋名", "Mai Danh Ẩn Tính": "隐姓埋名",
    "Kim Nguyên Bảo": "金元宝", "Tiền đồng": "铜钱", "Tiền Đồng": "铜钱", "Đồng thau": "黄铜",
    "Long Hổ lệnh": "龙虎令", "Túi hàng": "货袋", "Bào thương lệnh": "送药令", "Bào Thương": "送药",
    "Vận Lương": "运粮", "Tống Tửu": "送酒", "Tống Tứu": "送酒", "Tửu Xa": "酒车", "Tiêu Xa": "镖车",
    "Vô Gian Hành Giả": "无间行者", "Vũ La Thần Y": "羽罗神衣", "Viên Bồn": "圆盆", "Thiên Hạo Tâm": "天昊心",
    "Lục Châu": "绿珠", "Tẩy Tủy kinh": "洗髓经", "Sư phó thiếp": "师付帖", "Đồ đệ thiếp": "徒弟帖",
    "An Cư đồ": "安居图", "Tụ Tiên phủ": "聚仙斧", "Thần Dụ kính": "神谕镜", "Thần Dụ Kính": "神谕镜",
    "Mật tịch da dê": "羊皮秘籍", "Mật đồ da dê": "羊皮密图", "Mật tịch": "秘籍", "Bạch Trà": "白茶",
    "Đào mộc": "桃木", "Khê Thạch": "溪石", "Lò tinh luyện": "精炼炉", "Trứng Thông Linh": "通灵蛋",
    "Tiên Lộ": "仙露", "Vũ Thanh Linh": "武清灵", "Thông Phù": "通符", "Hấp Hồn phù": "吸魂符",
    "Hấp Hồn hồ lô": "吸魂葫芦", "Hấp Hồn": "吸魂", "Chấn Thiên Tiễn": "镇天箭", "Tam Tiêm Xoa": "三尖叉",
    "Râu Giao Long": "蛟龙须", "Giao Long": "蛟龙", "Trạnh Nanh thần thú": "獐牙神兽", "Trạnh Nanh": "獐牙",
    "Phong Thần Đài": "封神台", "Phong Thần đài": "封神台", "Vạn Tiên trận": "万仙阵", "Vạn Tiên Trận": "万仙阵",
    "Chu Tiên trận": "诛仙阵", "Lục Hồn Phiên": "六魂幡", "Thập Tuyệt trận": "十绝阵",
    "Thông Thiên giáo chủ": "通天教主", "Thông Thiên": "通天", "Xiển Giáo": "阐教", "Triệt Giáo": "截教",
    "Ngọc Hoàng": "玉皇", "Tây Phương giáo chủ": "西方教主", "Càn Khôn Xích": "乾坤尺", "Hỗn Thiên Lăng": "混天绫",
    "Hỏa Long Tiêu": "火龙镖", "Bạch Cốt phướn": "白骨幡", "Hạnh Hoàng Kỳ": "杏黄旗", "Càn Khôn khuyên": "乾坤圈",
    "Càn Khôn Khuyên": "乾坤圈", "Dây Khổn Tiên": "捆仙绳", "Dây Phược Long": "缚龙索", "Linh Lung Tháp": "玲珑塔",
    "Kim Bát Vu": "金钵盂", "Chấn Thiên Cung": "震天弓", "Bình Lưu Ly": "琉璃瓶", "Lưu Ly": "琉璃",
    "Ngọc Như ý": "玉如意", "Thanh Vân Kiếm": "青云剑", "Hỏa Tỳ Bà": "火琵琶", "An Mệnh Phù": "安命符",
    "Toàn Tâm Đinh": "攒心钉", "Hồng Hồ Lô": "红葫芦", "Lạc Hồn Chung": "落魂钟", "Ngọc Hư Phù": "玉虚符",
    "C.Độn Long": "遁龙桩", "Cọc Độn Long": "遁龙桩", "K.Chiếu Yêu": "照妖镜", "Kính Chiếu Yêu": "照妖镜",
    "P.Hỏa Luân": "风火轮", "Phong Hỏa Luân": "风火轮", "Phong Hỏa luân": "风火轮",
    "K.Quang Tỏa": "金光锉", "Kim Quang Tỏa": "金光锉", "Kim Quang Táa": "金光锉",
    "Ngũ Quang Thạch": "五光石", "Ngũ Quang thạch": "五光石",
    "Định Phong Châu": "定风珠", "Định Phong châu": "定风珠",
    "Hình Thiên ấn": "刑天印", "Hình Thiên": "刑天", "Âm Dương Kính": "阴阳镜", "Â.Dương Kính": "阴阳镜",
    "Túi Ngô Phong": "蜈蜂袋", "Bích Tỳ Bà": "碧琵琶", "Kim Cang Phách": "金刚帕", "Thái Dương Châm": "太阳针",
    "Bàn Cổ phướn": "盘古幡", "Bàn Cổ": "盘古", "ấm Vạn Nha": "万鸦壶", "Linh Lung": "玲珑",
    "Hồng T.Tinh": "红水晶", "T.Tinh": "水晶", "T.Điện": "天电", "TS. Thạch": "他山石",
    "Quy Chân Thạch": "归真石", "Đại địa": "大地", "Phong Bạo": "风暴", "Yêu đái": "腰带",
    "Yêu Đái": "腰带", "Đầu khôi": "头盔", "Phi Phong": "披风", "Khải Giáp": "铠甲", "Khải giáp": "铠甲",
    "Ngọc bội": "玉佩", "Ngọc Bội": "玉佩", "Kỳ Lân": "麒麟", "Huyền thiết": "玄铁", "Trảm Long": "斩龙",
    "Nguyên Thủy": "元始", "Thần Ưng": "神鹰", "Kim Ngưu": "金牛", "Hỗn Độn": "混沌", "Thao Thiết": "饕餮",
    "Đào Cơ": "桃矶", "Đảo Cơ": "岛矶", "Khốn Kỳ": "困旗", "Bạch Vân hồ điệp": "白云蝴蝶",
    "Thanh Vân hồ điệp": "青云蝴蝶", "hồ điệp": "蝴蝶", "Khuyển Văn Trụ": "犬纹柱", "Lực Sĩ Tế": "力士祭",
    "Thiên Ngân": "天银", "Bánh Trung Thu": "月饼", "Bánh": "饼", "Cửu Chuyển Bình": "九转瓶",
    "Chuyển Sinh Bình": "转生瓶", "Chứng Nhận": "凭证", "Chuyển Sinh": "转生", "Tụ Hồn Châu": "聚魂珠",
    "Tụ Hồn": "聚魂", "Hóa Thần Lệnh": "化神令", "Phong Thần Lệnh": "封神令", "Hộp Quà Phong Thần": "封神礼盒",
    "Hộp Quà Tân Thủ": "新手礼盒", "Đại Điêu Linh Phù": "大雕灵符", "Bàn Cổ Linh Phù": "盘古灵符",
    "Thông Thiên Linh Phù": "通天灵符", "Hàn Băng Nguyên Khí": "寒冰元气", "Sủi Cảo Toàn Gia Phúc": "全家福饺子",
    "Sủi Cảo": "饺子", "Tam Quang": "三光", "Thần Thủy": "神水", "Thần Mộc": "神木", "Ma Huyết": "魔血",
    "Côn Lôn kính": "昆仑镜", "Côn Lôn": "昆仑", "Hoàng Long Chân Nhân": "黄龙真人", "Hoàng Long": "黄龙",
    "Vân Trung Tử": "云中子", "Khương Tử Nha": "姜子牙", "Dương Tiễn": "杨戬", "Na Tra": "哪吒",
    "Lôi Chấn Tử": "雷震子", "Thổ Hành Tôn": "土行孙", "Xích Tùng Tử": "赤松子", "Dung Thành Tử": "容成子",
    "Nam Cực Tiên Ông": "南极仙翁", "Phổ Hiền": "普贤", "Nhiên Đăng": "燃灯", "Linh Bảo đại pháp sư": "灵宝大法师",
    "Linh Bảo": "灵宝", "Thiên Toán Tử": "天算子", "Hạc Lão Nhân": "鹤老人", "Tây Vương Mẫu": "西王母",
    "Vân Tiêu": "云霄", "Bích Tiêu": "碧霄", "Quỳnh Tiêu": "琼霄", "Triệu Công Minh": "赵公明",
    "Hoàng Thiên Hóa": "黄天化", "Trấn Nguyên Đại Tiên": "镇元大仙", "Trấn Nguyên": "镇元", "Sùng Thành": "崇城",
    "Sùng ứng Bưu": "崇应彪", "Cao Giác": "高角", "Bá Giám": "伯鉴", "Tân Miễn": "新免", "Văn Thái Sư": "闻太师",
    "Văn Thái sư": "闻太师", "Thương Thang": "商汤", "Hồ Hỷ Mị": "狐喜媚", "Dư Khánh": "余庆",
    "Tạp hóa Thương": "杂货商", "Sinh Hoạt Sư": "生活师", "Võ sư": "武师", "Truyền Lệnh Quan": "传令官",
    "Truyền lệnh quan": "传令官", "Thủ Khố": "库管", "Thủ khố": "库管", "Tỳ Bà": "琵琶",
    "Quỳnh Tiêu Nương Nương": "琼霄娘娘", "Vân Tiêu Nương Nương": "云霄娘娘", "Bích Tiêu Nương Nương": "碧霄娘娘",
    "Huyền Đô": "玄都", "Nhị Lang Thần": "二郎神", "Thác Tháp Lý Thiên Vương": "托塔李天王",
    "Lý Tịnh": "李靖", "Công Tử Hữu Huy": "公子友辉", "Lão Hồ Lô": "老葫芦", "Đại Phu": "大夫",
    "Tuyệt Long Lĩnh": "绝龙岭", "Thanh Đồng Sơn": "青铜山", "Bất Chu Sơn": "不周山", "Tây Côn Lôn": "西昆仑",
    "Trần Đường": "陈塘", "Mục Dã": "牧野", "Kỳ Sơn": "岐山", "Tam Sơn": "三山", "Hoang Mạc": "荒漠",
    "Hiên Viên động": "轩辕洞", "Băng Xuyên": "冰原", "Hàn Băng Trận": "寒冰阵", "Đông Hải": "东海",
    "Thủ Dương Sơn": "首阳山", "Vân Long đảo": "云龙岛", "Đông Doanh đảo": "东瀛岛", "Đông Doanh": "东瀛",
    "Phương Trượng Đảo": "方丈岛", "Bồng Lai Đảo": "蓬莱岛", "Khai Minh đảo": "开明岛", "Tam Tiên Đảo": "三仙岛",
    "Tam Tiên": "三仙", "Khổn Tiên Cung": "捆仙宫", "Khổn Tiên": "捆仙", "Diêu Trì": "瑶池",
    "Ngọc Hư Cung": "玉虚宫", "Xi Vưu Mộ": "蚩尤墓", "Xi Vưu": "蚩尤", "Triều Ca": "朝歌", "Tây Kỳ": "西岐",
    "Đồng Quan": "潼关", "Mạnh Tân": "孟津", "Khai Minh": "开明", "Phong Thần": "封神",
    "Thiên Cống": "天贡", "Xảo Đoạt Thiên Công": "巧夺天工", "Nữ Oa Thạc": "女娲石", "Nữ Oa": "女娲",
    "Chúc phúc của Nữ Oa": "女娲的祝福", "Linh Nguyện": "灵愿", "Tử Kim Hồ Lô": "紫金葫芦",
    "Chiêu Hồn phướn": "招魂幡", "Mã Đế": "马帝", "Chuộc Hồn đăng": "赎魂灯", "Băng Lang Vương": "冰狼王",
    "Liên Đăng Hộ sứ": "莲灯护使", "Liên Đăng": "莲灯", "Nhân Gian Hỏa": "人间火", "Bạch Thạch": "白石",
    "Thần Oanh": "神莺", "Cự Dã": "巨野", "Kim Hà quán": "金霞观", "Luyện Kim thuật": "炼金术",
    "Huyền Nữ Binh Pháp": "玄女兵法", "Huỳnh Đế Nội Kinh": "黄帝内经", "Ban Môn Lộng Phủ": "班门弄斧",
    "Phục Thế phủ": "伏世斧", "Ngô Long": "吴龙", "Dị nhân": "异人", "Trương Khuê": "张奎",
    "Cơ Phát": "姬发", "Trụ Vương": "纣王", "Đắc Kỷ": "妲己", "Hỏa Linh thánh mẫu": "火灵圣母",
    "Kim Quán": "金霞冠", "Hoàn Cẩu Tinh": "幻狗精", "Yến Sơn": "燕山", "Xạ Nhân": "射人",
    "Dị Dung Thuật": "易容术", "Thư tạo phản": "造反书", "Độc Lục quái": "毒鹿怪", "Giáp Vận Quan": "押运官",
    "Phong Bá": "风伯", "Thiếu Hạo": "少昊", "Hậu Thổ": "后土", "Khoa Phụ": "夸父", "Cộng Công": "共工",
    "Chúc Dung": "祝融", "Vật Tổ": "物祖", "Vật tổ": "物祖", "Tiêu Sư": "镖师", "Tổng Tiêu đầu": "总镖头",
    "Phù ấn Sư": "符印师", "Thầy bói": "算命先生", "Thầy Tướng Số": "相命先生", "Thầy tướng số": "相命先生",
    "Tinh Quan": "星官", "Kỳ sĩ": "骑士", "Thuyền phu": "船夫", "Lễ Quan": "礼官", "Tạp thương": "杂货商",
    "Tạp Hóa Thương": "杂货商", "Chủ Tửu Quán Tây Vực": "西域酒馆老板", "Chủ tửu quán": "酒馆老板",
    "Bích Du Nhân": "碧游仙人", "Bích Du Cung": "碧游宫", "Bích Du": "碧游", "Thủ khố": "库管",
    "Thần hành Lạc đà": "神行骆驼", "Lạc đà": "骆驼", "Phi Mao phù": "飞毛符",
    # 地气活动
    "Thạch Miêu": "石猫", "Linh Thạch": "灵石", "Địa Khí": "地气", "phe xanh": "绿方", "phe vàng": "黄方",
    "người trồng": "种植者", "Thu thập": "采集", "Trồng mầm": "种植树苗", "Thu Thập": "采集",
    "Người thu thập": "采集者", "Thu thập": "采集", "Khai khoáng": "挖矿", "chế tạo": "制造",
    "Gia công": "加工", "Luyện đơn": "炼丹", "Chế luyện": "炼制", "Nấu Nướng": "烹饪", "câu cá": "钓鱼",
    "thể lực": "体力", "tinh lực": "精力", "độ thuần thục": "熟练度", "Tôn sư": "宗师",
    # 通用单字/词
    "Quân": "君", "Thạch": "石", "Phù": "符", "Liệt": "烈", "Thiên": "天", "Hồng": "红", "Phá": "破",
    "Chấn": "震", "Uyên": "渊", "Triều": "朝", "Yêu": "妖", "Hỏa": "火", "Thương": "商", "Tâm": "心",
    "Hư": "虚", "Lôi": "雷", "Đán": "丹", "Tử": "子", "Nguyên": "元", "Chú": "咒", "Giáp": "甲",
    "Đái": "带", "Lạc": "落", "Trảm": "斩", "Hồn": "魂", "Hồ": "壶", "Tiên": "仙", "Kỳ": "旗",
    "Trùng": "重", "Như": "如", "ý": "意", "Ý": "意", "Khôi": "盔", "Dương": "阳", "Hộ": "护",
    "Thần": "神", "Xuyên": "穿", "Điệp": "蝶", "Thành": "城", "Chiến": "战", "Sùng": "崇", "Bá": "伯",
    "Bảo": "宝", "Tế": "祭", "Tân": "新", "Cuồng": "狂", "Phần": "焚", "Huyền": "玄", "Vũ": "武",
    "Liên": "连", "Mạnh": "孟", "Vân": "云", "Châu": "珠", "Thổ": "土", "Thủy": "水", "Địa": "地",
    "Sơn": "山", "Ngọc": "玉", "Băng": "冰", "Chân": "真", "Thôi": "摧", "Quán": "冠", "Thủ": "守",
    "Phách": "魄", "Kháng": "抗", "Tuyệt": "绝", "Hoàng": "皇", "Cơ": "机", "Điện": "电", "Cân": "巾",
    "Tịnh": "净", "Điền": "田", "Kết": "结", "Hoá": "化", "Pháp": "法", "Hoàn": "环", "Chúc": "祝",
    "Phúc": "福", "Tán": "散", "Tịch": "籍", "Lăng": "绫", "Tụ": "聚", "Đăng": "灯", "Cù": "驱",
    "Tô": "苏", "Khôn": "坤", "Nỗ": "弩", "Phược": "缚", "Lý": "履", "Lệnh": "令", "Tiêu": "标",
    "Hàn": "寒", "Nộ": "怒", "Hống": "轰", "Phệ": "噬", "ảnh": "影", "Ảnh": "影", "Thể": "体",
    "Luyện": "炼", "Ngục": "狱", "Thiểm": "闪", "Diệt": "灭", "Hoả": "火", "Đồ": "图", "Đằng": "腾",
    "Trụy": "坠", "Đào": "桃", "Trạch": "泽", "Hữu": "有", "Sậu": "飓", "Bạo": "暴", "Hoặc": "惑",
    "Võ": "武", "Cổ": "古", "Lộc": "禄", "Ngưu": "牛", "Định": "定", "Thảo": "草", "Khúc": "曲",
    "Càn": "乾", "Ứng": "应", "Trần": "陈", "Côn": "昆", "Bối": "贝", "Nhãn": "眼", "Huyễn": "幻",
    "Hà": "河", "Bất": "不", "Tốn": "巽", "Lân": "麟", "Âm": "阴", "Không": "空", "Trầm": "沉",
    "Tây": "西", "Xích": "赤", "Kính": "镜", "Phạn": "饭", "Khuyên": "圈", "Cầm": "琴", "Phục": "伏",
    "Đan": "丹", "Phát": "发", "Hình": "刑", "Thừa": "丞", "Lưu": "流", "Tôn": "尊", "Chuẩn": "准",
    "Đề": "提", "Trụ": "柱", "Thân": "身", "Ngũ": "五", "Diễm": "焰", "Nữ": "女", "Phiên": "幡",
    "Hùng": "雄", "Từ": "慈", "Ngư": "鱼", "Đơn": "丹", "Đô": "都", "Chiêu": "招", "Bạch": "白",
    "Đai": "带", "Bào": "袍", "Viên": "员", "Đại": "大", "Đoạn": "断", "Bà": "婆", "Vưu": "尤",
    "Cự": "巨", "Lục": "绿", "Cát": "吉", "Hạo": "浩", "Điêu": "雕", "Châm": "针", "Cốt": "骨",
    "Lộ": "露", "Thố": "兔", "Bản": "本", "Tuyền": "泉", "Sơ": "初", "Thiết": "铁", "đảo": "岛",
    "tông": "宗", "nhân": "人", "mông": "蒙", "cấp": "级", "Tiêm": "尖", "Phủ": "斧", "Hóa": "化",
    "Khương": "姜", "Lĩnh": "岭", "Đạo": "道", "Vệ": "卫", "Mị": "媚", "Vô": "无", "Tận": "尽",
    "Liễu": "柳", "Cựu": "旧", "Thúc": "叔", "Cửu": "九", "Tài": "才", "Chưởng": "掌", "Tư": "司",
    "Phiêu": "飘", "Trại": "寨", "Khốn": "困", "Diệm": "焰", "Ngộ": "悟", "Tính": "性", "Dã": "野",
    "Vương": "王", "Môn": "门", "Khốc": "哭", "Khải": "铠", "Sư": "师", "Kiếm": "剑", "Thú": "兽",
    "Hạc": "鹤", "Quảng": "广", "Cáo": "告", "Phụ": "父", "Bưu": "彪", "Dạ": "夜", "Thiếu": "少",
    "Công": "公", "Bàn": "盘", "Hạnh": "杏", "Vạn": "万", "Dây": "绳", "Tùng": "松", "Nhiếp": "摄",
    "Ấn": "印", "Triệu": "召", "Dịch": "驿", "Hương": "香", "Trúc": "竹", "Thụ": "树", "Tảo": "扫",
    "Quế": "桂", "Lô": "炉", "Toàn": "全", "Bốc": "卜", "Phác": "朴", "Thái": "太", "Thiếu": "少",
    "Miêu": "苗", "Mù": "木", "Đinh": "钉", "Trương": "张", "Khuê": "奎", "Triệt": "截", "Chí": "志",
    "Sứ": "使",
    # 23000 段技能/名称残留（复合）
    "Trung Tước": "中雀", "Bích Vũ Minh": "碧羽鸣", "Bích": "碧", "Vũ Minh": "羽鸣", "Minh Tôn": "鸣尊",
    "Quần Anh Hội": "群英会", "Anh Hội": "英会", "Đoạn Không Trảm": "断空斩", "Đoạn Không": "断空",
    "Tụ Hoa Hóa Thương": "聚花化商", "Tụ Hoa": "聚花", "Hóa Thương": "化商",
    "Cuồng Tâm Trảm": "狂心斩", "Cuồng Tâm": "狂心", "Thiên Hàn Trảm": "天寒斩", "Thiên Hàn": "天寒",
    "Thanh Lé": "青黎", "Lé": "黎",
    # 不周天关 / 清虚道尊 / 驱守剑仙（23040）
    "Bất Chu Thiên Quan": "不周天关", "Bất Chu Thiên": "不周天", "Thanh Hư Đạo Trưởng": "清虚道尊",
    "Thanh Hư": "清虚", "Đạo Trưởng": "道尊", "Khu Thủ Kiếm Tiên": "驱守剑仙", "Khu Thủ": "驱守",
    "Kiếm Tiên": "剑仙", "Dực": "翼", "Tước": "雀",
}

# Google 已知错译修正（整句级，基于原文含义）
FIX = [
    # (片段匹配, 替换为)
    ("星爵", "星君"),
    ("抗日旗帜", "抗娲大旗"),
    ("西奇", "西岐"),
    ("财主", "库管"),
    ("风神平台", "封神台"),
    ("苍穹云蝶", "青云蝴蝶"),
    ("该代币由", "令牌由"),
    ("十倍", "十大"),
]

# 缺失条目补译
FALLBACK = {
    "22017": "星君*玄铁甲*仙阶1(+1)",
    "22019": "星君*玄铁甲*仙阶3(+1)",
}


def _sort_keys():
    return sorted(TERM.keys(), key=lambda k: (-len(k), k))

# 规范化术语表：Google 同一术语的不同译法 -> 标准中文（保证全文一致）
CANON = {
    "镇魂石": "镇魂石", "魂铸石": "镇魂石", "魂石": "镇魂石",
    "摧风令": "摧风令", "摧风订单": "摧风令", "摧风指令": "摧风令", "摧风": "摧风令",
    "星际将军": "星君", "星爵": "星君",
    "神秘铁甲": "玄铁甲", "神秘铁": "玄铁", "玄铁甲": "玄铁甲",
    "原始道袍": "元始道袍", "元始道袍": "元始道袍",
    "雷霆结": "震雷结", "雷霆": "震雷", "震雷铠": "震雷铠",
    "灵魂珍珠": "聚魂珠", "聚魂珠": "聚魂珠", "救赎宝珠": "赎魂珠", "救赎珠": "赎魂珠",
    "伯乐之眼": "伯乐眼", "伯乐眼": "伯乐眼",
    "力士祭": "力士祭", "战士的牺牲": "力士祭",
    "观音水": "玉虚露",
    "巨型战斗爱情腰带": "麒麟战带", "狗狗图案爱心腰带": "犬纹腰带", "犬纹腰带": "犬纹腰带",
    "天空中的云": "青云", "云中": "云中",
    "环视之眼": "环观眼", "环观眼": "环观眼", "大地之眼": "大地眼", "暴风之眼": "暴风眼",
    "烈焰之眼": "烈焰眼", "风暴之眼": "风暴眼",
    "灵魂法术": "魂咒", "诛仙阵": "诛仙阵",
    "小金丹": "小红丹", "小红丹": "小红丹",
    "蓝宝石": "蓝宝石", "红宝石": "红宝石", "黄水晶": "黄水晶",
    "混元珠": "混元珠", "四象": "四象",
    "开天镜": "开天镜", "洗髓丹": "洗髓丹", "洗髓经": "洗髓经", "丹胚": "丹胚",
    "归真镜": "归真镜", "太极归真镜": "太极归真镜",
    "山石": "山石", "他山石": "他山石",
    "将军令": "将军令", "熔晶露": "熔晶露",
    "玉佩": "玉佩", "腰带": "腰带", "头盔": "头盔", "披风": "披风", "铠甲": "铠甲",
    "仙露": "仙露", "通灵蛋": "通灵蛋", "九转仙丹": "九转仙丹",
    "不朽等级": "仙阶", "仙人等级": "仙阶", "恶魔级": "魔阶", "恶魔（": "魔阶（",
    # 装备属性标签（1001 ini 装备详情模板）
    "防守": "防御", "防守力": "防御", "派系": "门派", "门派派系": "门派",
    "内部实力": "内力", "活力": "生命", "生命值": "生命", "耐久性": "耐久",
    "防雷保护": "雷防", "雷电保护": "雷防", "雷防护": "雷防",
    "所需等级": "等级要求", "所需级别": "等级要求", "级别要求": "等级要求",
    "恢复活力": "回复生命", "恢复生命": "回复生命", "回春之力": "回复生命",
    "重生点": "回复生命", "重量": "负重",
    "攻击力": "攻击", "攻击强度": "攻击", "魔法防御": "法防",
}

def apply_canon(text):
    for k, v in CANON.items():
        if k != v and k in text:
            text = text.replace(k, v)
    return text

def apply_terms(text):
    for k in _sort_keys():
        if k in text:
            # 只在前后不是 ASCII/越南字母时替换；中文视为边界，允许替换
            text = re.sub(r"(?<![A-Za-zÀ-ỹ])" + re.escape(k) + r"(?![A-Za-zÀ-ỹ])", TERM[k], text)
    return text

def apply_fix(text):
    for a, b in FIX:
        if a in text:
            text = text.replace(a, b)
    return text

def restore_symbols(vn, zh):
    """确保译文中包含原文全部符号占位符（数量一致）；缺失的在句首按原文顺序补回。"""
    from collections import Counter
    c_vn = Counter(c for c in vn if c in SYMS)
    c_zh = Counter(c for c in zh if c in SYMS)
    missing = c_vn - c_zh
    if missing:
        pre = "".join(c * missing[c] for c in vn if c in SYMS and missing[c] > 0)
        # 按原文出现顺序
        ordered = [c for c in vn if c in SYMS and missing[c] > 0]
        pre = "".join(ordered)
        zh = pre + zh
    return zh

# 源级权威词表：源越南语精确命中时，整条用标准译名覆盖（不依赖 Google）
SOURCE_OVERRIDE = {
    "Thập đại cao thủ": "十大高手", "cấp": "等级", "Thập đại phú hào": "十大富豪",
    "Thập đại sát thủ": "十大杀手", "Tài phú binh giáp": "太尉兵甲", "tài phú": "太尉",
    "Hoang dã cao thủ": "荒野高手", "Hoang dã phú hào": "荒野富豪", "Thập đại danh nhân": "十大名人",
    "Phúc duyên": "福缘", "phúc duyên": "福缘", "Thách thức thời gian": "时间挑战",
    "Môn phái cao thủ": "门派高手", "Thiếu Lâm": "少林", "Thiên Vương": "天王", "Đường Môn": "唐门",
    "Thúy Yên": "翠烟", "Thiên Nhẫn": "天忍", "Võ Đang": "武当", "Côn Lôn": "昆仑",
    "Môn phái phú hào": "门派富豪", "Thập đại bang hội": "十大帮会", "Đẳng cấp": "等级",
    "Thành viên": "成员", "người": "人", "Cèng hiƠn": "贡献", "cèng hiƠn": "贡献",
    "Cống hiến": "贡献", "cống hiến": "贡献",
    "Xích Đồng Đao": "赤铜刀", "Vũ Khí Hoàng Kim cấp 40": "黄金武器40级", "Hàng Ma Đao": "降魔刀",
    "Vũ Khí Hoàng Kim cấp 50": "黄金武器50级", "Tiếu Thiên Đao": "啸天刀", "Sáng Thế Kích": "创世戟",
    "Tử Dương Kiếm": "紫阳剑", "Côn Ngô Kiếm": "昆吾剑", "Phục Thế Phủ": "伏世斧",
    "Lạc Hồn Phủ": "落魂斧", "Giao HảI Đao": "交海刀", "Vũ Khí Hoàng Kim cấp 60": "黄金武器60级",
    "Đả Thần Tiên": "打神鞭", "Vũ Khí Hoàng Kim cấp 70": "黄金武器70级", "Ngân Tiêm Kích": "银尖戟",
    "Điểm Tướng Kích": "点将戟", "Độn Long Kiếm": "遁龙剑", "Cự Khuyết Kiếm": "巨阙剑",
    "Thất Bảo Phủ": "七宝斧", "Tụ Tiên Phủ": "聚仙斧", "Đoạt Cung Đao": "夺宫刀",
    "Vũ Khí Hoàng Kim cấp 80": "黄金武器80级", "Trục Nhật Kiếm": "逐日剑", "Vũ Khí Hoàng Kim cấp 90": "黄金武器90级",
    "Tuyên Hoa Phủ": "宣花斧", "Khai Thiên Phủ": "开天斧", "Ngô Câu Kiếm": "吴钩剑",
    "Kim Quang Kiếm": "金光剑", "Tuyệt Tiên Phủ": "绝仙斧", "Hỗn Thiên Phủ": "混天斧",
    "Viêm Đế Kiếm": "炎帝剑", "Vũ Khí Hoàng Kim cấp 100": "黄金武器100级", "Trạm Kim Phủ": "斩金斧",
    "TháI Cực Kiếm": "太极剑", "Diệt Thần Phủ": "灭神斧", "Thanh Linh Ngọc Bội": "青灵玉佩",
    "Ngọc Bội": "玉佩", "Huyền Vũ Ngọc Bội": "玄武玉佩", "Phục Hổ Ngọc Bội": "伏虎玉佩",
    "Hoàng Huyết Ngọc Bội": "皇血玉佩", "Tinh Cang Giáp": "星罡甲", "Trang Bị Lục cấp 60": "绿色装备60级",
    "Tinh Cang Phi Phong": "星罡披风", "Thái ất Đạo Bào": "太乙道袍", "Thái ất Lệnh": "太乙令",
    "Giác Thú Hộ Giáp": "角兽护甲", "Giác Thú Kết": "角兽结", "Khai Thiên Giáp": "开天甲",
    "Trang bị Lục cấp 80": "绿色装备80级", "Khai Thiên Phi Phong": "开天披风", "Thông Thiên Đạo Bào": "通天道袍",
    "Thông Thiên Lệnh": "通天令", "Lam Điêu Hộ Giáp": "蓝雕护甲", "Lam Điêu Kết": "蓝雕结",
    "Sách kỹ năng 70 - 90": "技能书70-90", "Sách Chư Hầu (khởi)": "诸侯书(初)", "Sách Chư Hầu (thừa)": "诸侯书(承)",
    "Hoàng Kim Chấn Đán Giáp": "黄金震丹甲", "Trang bị Lục cấp 100": "绿色装备100级",
    "Chán Đán Phi Phong": "震丹披风", "Hồng Quân Đạo Bào": "鸿钧道袍", "Hồng Quân Lệnh": "鸿钧令",
    "Kháng Long Hộ Giáp": "亢龙护甲", "Kháng Long Kết": "亢龙结", "Cấp độ Vạn Tiên Trận": "万仙阵等级",
    "Lâm Tiên Lộ": "临仙路", "Đồ phổ Trang Bị Cam Phá Quân": "破军橙色装备图样",
    "Tướng Quân Lệnh": "将军令", "Tiền vạn": "钱万", "Bá Lạc Nhãn": "伯乐眼", "Quẻ Càn": "乾卦",
    "Sách kỹ năng 1 - 90": "技能书1-90", "Thần Thụ Lệnh": "神树令", "Không hạn chế": "不限",
    "Binh khí ngắn": "短兵器", "Binh khí dài": "长兵器", "Bảo kiếm hệ lôi": "雷系宝剑",
    "Bảo kiếm hệ thổ": "土系宝剑", "Bảo kiếm hệ băng": "冰系宝剑", "Bảo kiếm hệ hỏa": "火系宝剑",
    "Phi tiêu": "飞镖", "Phi đao": "飞刀", "Pháp khí": "法器", "Kỹ năng tấn công vật lý": "物理攻击技能",
    "Kỹ năng bùa pháp": "符法技能", "Tấn công Vật lý": "物理攻击", "Hỗ trợ bị động": "被动辅助",
    "Chủ động hỗ trợ": "主动辅助", "Kỹ năng hỗ trợ": "辅助技能", "Hỗ trợ phòng ngự bị động": "被动防御辅助",
    "Chủ động hỗ trợ phòng ngự": "主动防御辅助", "Hỗ trợ chiến đấu bị động": "被动战斗辅助",
    "Chủ động hỗ trợ chiến đấu": "主动战斗辅助", "Hỗ trợ chiến đấu": "战斗辅助", "Tăng tính chủ động": "主动加成",
    "Phục hồi thuộc tính bị động": "被动属性恢复", "Chủ động Phục hồi thuộc tính": "主动属性恢复",
    "Phục hồi thuộc tính": "属性恢复", "Kỹ năng chiến đấu Tiên thuật": "仙术战斗技能",
    "Kỹ năng ném Tuyết": "掷雪技能", "Kỹ năng Quỷ": "鬼技", "Kỹ năng bắt Quỷ": "捉鬼技能",
    "Kỹ năng thiên phú": "天赋技能", "Mỉm cười": "微笑", "Giật mình": "惊讶", "Mặt quỷ": "鬼脸",
    "Buồn bã": "悲伤", "Không lời": "无言", "Chán nản": "沮丧", "Bị đánh bại": "战败",
    "Bảo mật": "保密", "Nghe tôi nói": "听我说", "Thân mật 1": "亲密1", "Thân mật 2": "亲密2",
    "Nước miếng": "口水", "Tàn khốc": "残酷", "Bị đánh": "挨打", "Khôi hài": "滑稽",
    "Nháy mắt": "眨眼", "Cười ngớ ngẩn": "傻笑", "Nghịch ngợm": "调皮", "Chảy mồ hôi": "流汗",
    "Ngạo mạn": "傲慢", "Ngủ thiếp đi": "睡着", "Căm ghét": "憎恨", "Lạnh giá": "严寒",
    "Bịt miệng": "捂嘴", "Giận dữ": "愤怒", "Giả khùng": "装傻", "Nhận lỗi": "认错",
    "Tìm kiếm": "寻找", "Bĩu môi": "撇嘴", "Điện thoại": "手机", "Hoa tươi": "鲜花",
    "Đầu heo": "猪头", "Khóc thảm": "痛哭", "Đa tình": "多情", "Chóng mặt": "头晕",
    "Bốc hỏa": "冒火", "Tạm biệt": "再见", "Phong lưu": "风流", "Rạng rỡ": "容光焕发",
    "Mệt mỏi": "疲惫", "Đánh yêu": "打妖", "Huynh đệ": "兄弟", "Đồng tâm": "同心",
    "Cùng hát": "一起唱", "Trường sinh": "长生", "1 Phó Bang chủ": "1名副帮主", "Trưởng lão": "长老",
    "4 vị Trưởng Lão": "4位长老", "Đẳng cấp bang hội +1, thành viên tăng thêm:": "帮会等级+1，成员增加：",
    "Hương chủ": "香主", "Vi Tiểu Bảo chính là đây": "韦小宝就是这里", "Bia đỡ đạn": "挡箭牌",
    "Dao Trảm Tán": "刀斩伞", "Lục Tùng Thạch": "六松石", "Pháp Bảo 1x": "法宝1x", "Pháp Bảo 3x": "法宝3x",
    "Liễu Mộc": "柳木", "Hải Hồn": "海魂", "Pháp Bảo 5x": "法宝5x", "Hỏa Linh Phù": "火灵符",
    "Chìa Khóa Linh Tê": "灵犀钥匙", "Hỏa Linh": "火灵", "Bùa Khai Khoáng": "挖矿符",
    "Biến thân phù (ngẫu nhiên)": "变身符(随机)", "Ngọc Định Tán": "玉定散", "Lò Luyện Đơn": "炼丹炉",
    "Nữ Oa Thạch": "女娲石", "Khao Quân Lệnh": "犒军令", "Thưởng Kim Bài": "赏金牌",
    "Hoàn Quan Nhãn": "环观眼", "Phác Ngọc": "朴玉", "Đại Địa Nhãn": "大地眼", "Ngọc Cam Tán": "玉甘散",
    "Dao Tinh Tán": "刀星散", "Ngọc ách Tán": "玉厄散", "Điểm Kinh Nghiệm": "经验点", "Thủy Hồn": "水魂",
    "Di Ngoại Phù": "移外符", "Địa Linh": "地灵", "Dụ Hồn Hương": "引魂香", "Chỉ Nhân": "纸人",
    "La Hán Hiệu Giác": "罗汉号角", "Niết Bàn Chú": "涅槃咒", "Sách Chư Hầu (mảnh)": "诸侯书(碎片)",
    "Thưởng thêm": "额外奖励", "Điểm danh bù": "补签", "Mốc không hợp lệ": "里程碑无效",
    "Bạn chưa điểm danh đủ để nhận mốc này": "签到不足，无法领取该里程碑",
    "Bạn cần nhận mốc trước đó trước khi nhận mốc này": "领取前需先领取上一个里程碑",
    "Bạn đã nhận mốc này": "你已领取过该里程碑", "Chủ nhật": "周日", "Điểm danh": "签到",
    "Điểm danh thành công:": "签到成功：", "Chuỗi báo danh liên tục:": "连续签到：",
    "Số lượt điểm danh bù còn lại": "剩余补签次数",
}

# ---------------- 模板/装备名组件字典（源文本结构化直译） ----------------
EQ = {
    # 套装名/前缀
    "Tinh Quân": "星君", "Huyền Thiết": "玄铁", "Hỗn Nguyên": "混元", "Chấn Lôi": "震雷",
    "Hoàng Kim": "黄金", "Tinh Cang": "星罡", "Khai Thiên": "开天", "Thông Thiên": "通天",
    "Hồng Quân": "鸿钧", "Kháng Long": "亢龙", "Lam Điêu": "蓝雕", "Giác Thú": "角兽",
    "Thái ất": "太乙", "Phục Hổ": "伏虎", "Huyền Vũ": "玄武", "Hoàng Huyết": "皇血",
    "Thanh Linh": "青灵", "Cự Đấu": "巨斗", "Vũ Khúc": "武曲", "Lâm Binh": "临兵",
    "Kỳ Lân": "麒麟", "Trảm Long": "斩龙", "Chấn Đán": "震丹", "Thiên Quyền": "天权",
    "Vân Trung": "云中", "Quảng Thành": "广成", "Xích Tùng": "赤松", "Cửu Cung": "九宫",
    "Chuẩn Đề": "准提", "Nguyên Thủy": "元始", "Thiên Lộc": "天禄", "Thánh Diệu": "圣耀",
    "Hư Nghi": "虚疑", "Loan Vũ": "鸾舞", "Thần Hoàng": "神皇", "Phù Lê": "符雷",
    "Hoàng Minh": "皇明", "Cang Long": "罡龙", "Khuyển Văn": "犬纹", "Báo Thần": "豹神",
    "Hổ Đầu": "虎头", "Thần Ưng": "神鹰", "Hỗn Độn": "混沌", "Viêm Đế": "炎帝",
    "Thất Bảo": "七宝", "Khai Thiên Phủ": "开天斧", "Tụ Tiên": "聚仙", "Thánh": "圣",
    "Liệt": "烈", "Chiến": "战", "Huỷn Khung": "玄空", "Huyền Khung": "玄空",
    "Cửu Tuyệt Lăng Thiên": "九绝凌天", "Thiên Thù": "天枢", "Vô Tần": "无尘",
    "Minh Quang": "明光", "Tuyệt Tiên": "绝仙", "Hỗn Thiên": "混天", "Trạm Kim": "斩金",
    "Côn Ngô": "昆吾", "Giao HảI": "交海", "Đả Thần": "打神", "Ngân Tiêm": "银尖",
    "Điểm Tướng": "点将", "Độn Long": "遁龙", "Cự Khuyết": "巨阙", "Đoạt Cung": "夺宫",
    "Trục Nhật": "逐日", "Tuyên Hoa": "宣花", "Ngô Câu": "吴钩", "Kim Quang": "金光",
    "Đả Thần Tiên": "打神鞭", "Hàng Ma": "降魔", "Tiếu Thiên": "啸天", "Sáng Thế": "创世",
    "Tử Dương": "紫阳", "Lạc Hồn": "落魂", "Phục Thế": "伏世", "Xích Đồng": "赤铜",
    # 部位/类型
    "Phi Phong": "披风", "Đạo Bào": "道袍", "Hộ Giáp": "护甲", "Ngọc Bội": "玉佩",
    "Chiến Ngoa": "战靴", "Yêu Đái": "腰带", "Lệnh": "令", "Kết": "结", "Giáp": "甲",
    "Khôi": "盔", "Trụ": "胄", "Cân": "巾", "Kích": "戟", "Đao": "刀", "Kiếm": "剑",
    "Phủ": "斧", "Tiên": "鞭", "Tán": "伞", "Phi Tiêu": "飞镖", "Phi Đao": "飞刀",
    "Pháp Khí": "法器", "Vũ Khí": "武器", "Trang Bị": "装备", "Binh Khí": "兵器",
    "Bảo Kiếm": "宝剑", "hệ lôi": "雷系", "hệ thổ": "土系", "hệ băng": "冰系", "hệ hỏa": "火系",
    "hệ": "系",
    # 阶数/等级
    "Ma Cấp": "魔阶", "Tiên Cấp": "仙阶", "Cấp": "阶", "cấp": "阶",
    # 描述
    "Hỗ trợ tấn công": "攻击辅助", "Hỗ trợ phòng thủ": "防御辅助", "Hỗ trợ": "辅助",
    "tấn công": "攻击", "phòng thủ": "防御",
    # 部件说明
    "Phi Phong Khôi trong bộ": "套装中的披风", "Lệnh trong bộ": "套装中的令", "Kết trong bộ": "套装中的结",
    "trong bộ": "套装中的",
    "trang bị": "装备", "nhiệm vụ": "任务", "N.v": "任务", "ngẫu nhiên": "随机",
    "Bùa": "符", "Phù": "符", "Chú": "咒",
    "cấp 40": "40级", "cấp 50": "50级", "cấp 60": "60级", "cấp 70": "70级",
    "cấp 80": "80级", "cấp 90": "90级", "cấp 100": "100级",
    "Vũ Khí Hoàng Kim cấp": "黄金武器", "Trang Bị Lục cấp": "绿色装备", "Trang bị Lục cấp": "绿色装备",
    "Sách kỹ năng": "技能书", "Sách Chư Hầu": "诸侯书", "Hoàng Kim": "黄金",
}

EQ_KEYS = sorted(EQ.keys(), key=lambda k: (-len(k), k))

def translate_template(src):
    """对装备/技能模板名做源文本结构化直译；不是模板则返回 None。"""
    s = src
    if not (("*" in src) or ("Cấp" in src and "(" in src)):
        return None
    out = s
    for k in EQ_KEYS:
        out = out.replace(k, EQ[k])
    # 清理 OCR 噪声占位符
    out = re.sub(r"[ƠƯẼỄộộ]", "", out)
    # 规整空格：汉字间、汉字与数字/符号间的空格去掉
    out = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", out)
    out = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[0-9(\*+])", "", out)
    out = re.sub(r"(?<=[0-9)\*+])\s+(?=[\u4e00-\u9fff(])", "", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out

def main():
    import openpyxl
    from collections import Counter
    global XLSX
    os.makedirs(OUT, exist_ok=True)
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        XLSX = sys.argv[1]
    term_hits = Counter()
    wb = openpyxl.load_workbook(XLSX, read_only=True)
    ws = wb[SHEET]
    trans = {}
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        if row[0] is None:
            continue
        trans[str(row[0]).strip()] = ("" if row[1] is None else str(row[1]))
    wb.close()

    # 读取原文
    files = sorted(os.listdir(CHUNKS), key=lambda x: int(x.split("_")[-1].split(".")[0]))
    orig_order = []
    orig_map = {}
    for fn in files:
        with open(os.path.join(CHUNKS, fn), encoding="utf-8-sig", newline="") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if not row:
                    continue
                iid = row[0].strip()
                txt = row[1] if len(row) > 1 else ""
                orig_order.append((fn, iid, txt))
                orig_map[iid] = txt

    report = {"files": 0, "entries": 0, "terms_applied": 0, "symbols_restored": 0,
              "vietnamese_left_after": 0, "fallback_used": 0}
    vi = re.compile(r"[àáảãạăắằẳẵặâấầẩẫậèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵđ]", re.I)

    # 按文件分组输出
    per_file = {}
    for fn, iid, txt in orig_order:
        zh = trans.get(iid)
        if zh is None:
            zh = FALLBACK.get(iid, "")
            report["fallback_used"] += 1
        before = zh
        # 源级覆盖：源文本精确命中权威词表时，直接采用标准译名
        src_key = txt.strip()
        if src_key in SOURCE_OVERRIDE:
            zh = SOURCE_OVERRIDE[src_key]
        else:
            tpl = translate_template(src_key)
            # 仅当模板翻译完全无越语残留时才采用，否则回退 Google 管线
            if tpl is not None and not vi.search(tpl):
                zh = tpl
        zh = apply_terms(zh)
        zh = apply_canon(zh)
        zh = apply_fix(zh)
        if zh != before:
            report["terms_applied"] += 1
        before_sym = "".join(c for c in zh if c in SYMS)
        zh = restore_symbols(txt, zh)
        if "".join(c for c in zh if c in SYMS) != before_sym:
            report["symbols_restored"] += 1
        zh = zh.strip()
        if vi.search(zh):
            report["vietnamese_left_after"] += 1
        per_file.setdefault(fn, []).append({"id": iid, "text": zh})

    for fn, entries in per_file.items():
        num = fn.split("_")[-1].split(".")[0]
        outpath = os.path.join(OUT, "updatefs_localization_%s.json" % num)
        with open(outpath, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)
        report["files"] += 1
        report["entries"] += len(entries)

    # 输出质检报告
    with open(os.path.join(OUT, "_quality_report.txt"), "w", encoding="utf-8") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2))

    # 输出润色后的 xlsx（Sheet「本土化」，A=id，B=润色中文）
    import openpyxl as _ox
    wb = _ox.Workbook()
    ws = wb.active
    ws.title = SHEET
    ws.append(["id", "text_zh"])
    for fn, iid, txt in orig_order:
        zh = per_file[fn][[e["id"] for e in per_file[fn]].index(iid)]["text"]
        ws.append([iid, zh])
    wb.save(XLSX_OUT)
    print("xlsx saved:", XLSX_OUT)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("OK. files:", report["files"], "entries:", report["entries"])

if __name__ == "__main__":
    main()
