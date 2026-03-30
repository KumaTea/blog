# Apple Pay 绑卡失败原因速查

先上目前绑卡截图镇楼

![](img/apple-wallet.jpg)

## 常见错误

### 你的发卡机构尚不支持此卡片

> Your issuer does not yet offer support for this card

![](img/err-not-support.jpg)

* 正在绑定的是中国大陆发行的非银联卡

### 卡的设备限制

> Card device limit

![](img/err-limit.jpg)

* 已经在其他手机绑定了改卡，需解绑
  * 如果确认已解绑多余设备， [可尝试等待3天](https://discussionschinese.apple.com/thread/253116288?answerId=255886195322#255886195322)

### 卡信息无效

> Invalid card

![](img/err-invalid.jpg)

* 卡片未激活
  * 有些卡，如农行的信用卡，面签完还要在手机银行再激活一次，尚未激活就会出现这个错误信息，可以进入手机银行查看卡片激活状态 
* 该卡号今日 [已失败5次](https://www.jimmytian.com/archives/solve-apple-pay-cant-add-boc-card.html#0x03-%E5%86%8D%E6%AC%A1%E6%89%93%E7%94%B5%E8%AF%9D%E5%88%B0%E9%93%B6%E8%81%94) ，明日再试
* 未插卡
  * 部分银行（如工商银行）要求添加银联信用卡时，手机插入并启用银行账户绑定的手机号SIM卡，可能在绑定过程中验证SIM卡信息

### 未添加此卡

> Could not add card
> 
> Card not added

![](img/err-could-not-add.jpg)

![](img/err-not-added.jpg)

* 原因不明，建议跟客服对线
  * 关于中行疑难杂症，请见下文

## 客服聊天记录

建议：先找银联问错误代码，再找对应银行；

银联首选云闪付线上客服，银行首选电话客服。

### 银联

> 碰到绑不上的问题可以先向银联咨询错误代码

![](img/chat-up-agent.jpg)

### 中国银行

> 中行电话客服才有足够权限，在线客服只能问到通用信息

![](img/chat-boc-bot.jpg)

![](img/chat-boc-agent.jpg)

* 每人无论单个或多个设备，最多存在 **3张** 已绑定的中行借记卡 (即包含 [同一张卡绑定3台设备的情况](https://www.jimmytian.com/archives/solve-apple-pay-cant-add-boc-card.html#comment-201))
* [有评论](https://www.jimmytian.com/archives/solve-apple-pay-cant-add-boc-card.html#comment-212) 称，中行最多绑定两张卡。 **我自己的情况正是这样**
* 所有银行卡合计超出上限 (iOS 17 以上 [移除了](https://t.me/DocOfCard/2019) 这个上限)

## 参考资料

* [中国银行绑定 Apple Pay 失败的解决方法](https://www.jimmytian.com/archives/solve-apple-pay-cant-add-boc-card.html)

> 我与该博主相似的地方有：
> * 第一次绑卡时收到了银联的风控电话
> * 绑卡失败代码都是 93608

* [钱包解绑银行卡后无法再重新绑定，提示设备限制](https://discussionschinese.apple.com/thread/253116288)
