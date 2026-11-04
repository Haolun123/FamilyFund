# 第十人系统：已移除

2026-11-04按用户要求移除10th Man功能：Dashboard标签页、专用审查模块、MCP工具`ask_tenth_man`及对应测试已删除。

已有历史报告及iCloud配置文件保留。AI周度评估继续使用`config/tenth_man_config.json`；该文件名为历史兼容名称，不再代表第十人功能入口。

验证：入口注册及Tab列表回归测试、现有MCP输出、AI周评与Father页面等相关测试20项通过；2项目标配置测试因缺少config目录失败，使用改动前的测试独立复现，属于既有问题。Dashboard与MCP源码编译通过。
