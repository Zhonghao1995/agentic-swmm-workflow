# Run from PowerShell to register Agentic SWMM MCP servers with Codex.
# Existing servers with the same names may need `codex mcp remove <name>` first.
codex mcp add swmm-builder -- '/opt/homebrew/Cellar/node@22/22.22.0/bin/node' '/Users/zhonghao/Desktop/Codex Project/Agentic SWMM open soure/scripts/run_mcp_server.mjs' 'swmm-builder'
codex mcp add swmm-calibration -- '/opt/homebrew/Cellar/node@22/22.22.0/bin/node' '/Users/zhonghao/Desktop/Codex Project/Agentic SWMM open soure/scripts/run_mcp_server.mjs' 'swmm-calibration'
codex mcp add swmm-climate -- '/opt/homebrew/Cellar/node@22/22.22.0/bin/node' '/Users/zhonghao/Desktop/Codex Project/Agentic SWMM open soure/scripts/run_mcp_server.mjs' 'swmm-climate'
codex mcp add swmm-gis -- '/opt/homebrew/Cellar/node@22/22.22.0/bin/node' '/Users/zhonghao/Desktop/Codex Project/Agentic SWMM open soure/scripts/run_mcp_server.mjs' 'swmm-gis'
codex mcp add swmm-network -- '/opt/homebrew/Cellar/node@22/22.22.0/bin/node' '/Users/zhonghao/Desktop/Codex Project/Agentic SWMM open soure/scripts/run_mcp_server.mjs' 'swmm-network'
codex mcp add swmm-params -- '/opt/homebrew/Cellar/node@22/22.22.0/bin/node' '/Users/zhonghao/Desktop/Codex Project/Agentic SWMM open soure/scripts/run_mcp_server.mjs' 'swmm-params'
codex mcp add swmm-plot -- '/opt/homebrew/Cellar/node@22/22.22.0/bin/node' '/Users/zhonghao/Desktop/Codex Project/Agentic SWMM open soure/scripts/run_mcp_server.mjs' 'swmm-plot'
codex mcp add swmm-runner -- '/opt/homebrew/Cellar/node@22/22.22.0/bin/node' '/Users/zhonghao/Desktop/Codex Project/Agentic SWMM open soure/scripts/run_mcp_server.mjs' 'swmm-runner'
