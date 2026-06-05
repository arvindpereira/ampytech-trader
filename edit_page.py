import os

filepath = "/Users/arvind/code/ampytech-trader/frontend/src/app/page.tsx"

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add Cpu icon import
old_import = "import React, { useState, useEffect } from 'react';\nimport {"
new_import = "import React, { useState, useEffect } from 'react';\nimport { Cpu } from 'lucide-react';\nimport {"
content = content.replace(old_import, new_import, 1)

# 2. Add appMode state
old_state = "  const [perfMode, setPerfMode] = useState<'live' | 'replay'>('live');"
new_state = "  const [perfMode, setPerfMode] = useState<'live' | 'replay'>('live');\n  const [appMode, setAppMode] = useState<'real' | 'simulated'>('real');"
content = content.replace(old_state, new_state, 1)

# 3. Replace fetchData method
# const mVal declaration at the start of try block:
old_try = "  const fetchData = async () => {\n    setLoading(true);\n    try {"
new_try = "  const fetchData = async () => {\n    setLoading(true);\n    try {\n      const mVal = appMode === 'real' ? 'real' : 'replay';"
content = content.replace(old_try, new_try, 1)

# suggestions fetch:
old_sug = "      // 1. Fetch Suggestions\n      const sugRes = await fetch('http://localhost:8008/api/suggestions');"
new_sug = "      // 1. Fetch Suggestions\n      const sugRes = await fetch(`http://localhost:8008/api/suggestions?mode=${mVal}`);"
content = content.replace(old_sug, new_sug, 1)

# suggestions check:
old_check = """      if (sugRes.ok) {
        const sugData = await sugRes.json();
        setSuggestions(sugData.short_term_suggestions);
        setAllocations(sugData.long_term_allocation);
        setRegime(sugData.date ? sugData.regime : 'growth');
        setDate(sugData.date || '');
        setBackendOnline(true);
      } else {
        throw new Error("Backend Suggestions Offline");
      }"""

new_check = """      if (sugRes.ok) {
        const sugData = await sugRes.json();
        setSuggestions(sugData.short_term_suggestions || []);
        setAllocations(sugData.long_term_allocation || []);
        setRegime(sugData.date ? sugData.regime : 'growth');
        setDate(sugData.date || '');
        setBackendOnline(true);
      } else {
        if (appMode === 'real') {
          setSuggestions([]);
          setAllocations([]);
          setRegime('growth');
          setDate('');
          setBackendOnline(true);
        } else {
          throw new Error("Backend Suggestions Offline");
        }
      }"""
content = content.replace(old_check, new_check, 1)

# performance curve:
old_perf = "      // 2. Fetch Performance Curve based on mode\n      const perfRes = await fetch(`http://localhost:8008/api/performance?mode=${perfMode}`);"
new_perf = "      // 2. Fetch Performance Curve based on mode\n      const perfModeQuery = appMode === 'real' ? 'live' : perfMode;\n      const perfRes = await fetch(`http://localhost:8008/api/performance?mode=${perfModeQuery}`);"
content = content.replace(old_perf, new_perf, 1)

# holdings fetch:
old_hold = "      // 4. Fetch User Holdings & Virtual Account\n      const holdRes = await fetch('http://localhost:8008/api/holdings');"
new_hold = "      // 4. Fetch User Holdings & Virtual Account\n      const holdRes = await fetch(`http://localhost:8008/api/holdings?mode=${mVal}`);"
content = content.replace(old_hold, new_hold, 1)

# account fetch:
old_acc = "const accRes = await fetch('http://localhost:8008/api/virtual_alpaca/v2/account');"
new_acc = "const accRes = await fetch(`http://localhost:8008/api/virtual_alpaca/v2/account?mode=${mVal}`);"
content = content.replace(old_acc, new_acc, 1)

# positions fetch:
old_vpos = "      // 5. Fetch Virtual Positions\n      const vposRes = await fetch('http://localhost:8008/api/virtual_alpaca/v2/positions');"
new_vpos = "      // 5. Fetch Virtual Positions\n      const vposRes = await fetch(`http://localhost:8008/api/virtual_alpaca/v2/positions?mode=${mVal}`);"
content = content.replace(old_vpos, new_vpos, 1)

# sentiment fetch:
old_sent = "      // 6. Fetch Sentiment list\n      const sentRes = await fetch('http://localhost:8008/api/sentiment');"
new_sent = "      // 6. Fetch Sentiment list\n      const sentRes = await fetch(`http://localhost:8008/api/sentiment?mode=${mVal}`);"
content = content.replace(old_sent, new_sent, 1)

# catch block in fetchData:
old_catch = """    } catch (err) {
      console.warn("FastAPI backend is offline. Loading simulated local values.");
      setBackendOnline(false);
      loadMockData();
    }"""
new_catch = """    } catch (err) {
      console.warn("FastAPI backend is offline. Loading simulated local values.");
      setBackendOnline(false);
      if (appMode !== 'real') {
        loadMockData();
      } else {
        setSuggestions([]);
        setAllocations([]);
        setPerfCurve([]);
        setHoldings([]);
        setVirtualPositions([]);
        setSentimentList([]);
        setAccountCash(0);
        setAccountEquity(0);
      }
    }"""
content = content.replace(old_catch, new_catch, 1)

# 4. Replace fetchSources method
old_fetchSources = """  const fetchSources = async (ticker: string) => {
    setLoadingSources(true);
    try {
      const res = await fetch(`http://localhost:8008/api/sentiment/sources?ticker=${ticker}`);
      if (res.ok) {
        const data = await res.json();
        setSentSources(data.sources || []);
      } else {
        loadMockSources(ticker);
      }
    } catch (err) {
      loadMockSources(ticker);
    } finally {
      setLoadingSources(false);
    }
  };"""

new_fetchSources = """  const fetchSources = async (ticker: string) => {
    setLoadingSources(true);
    try {
      const res = await fetch(`http://localhost:8008/api/sentiment/sources?ticker=${ticker}&mode=${appMode === 'real' ? 'real' : 'replay'}`);
      if (res.ok) {
        const data = await res.json();
        setSentSources(data.sources || []);
      } else {
        if (appMode !== 'real') {
          loadMockSources(ticker);
        } else {
          setSentSources([]);
        }
      }
    } catch (err) {
      if (appMode !== 'real') {
        loadMockSources(ticker);
      } else {
        setSentSources([]);
      }
    } finally {
      setLoadingSources(false);
    }
  };"""
content = content.replace(old_fetchSources, new_fetchSources, 1)

# 5. Update useEffect dependencies
old_effect = """  useEffect(() => {
    fetchData();
  }, [perfMode]);"""
new_effect = """  useEffect(() => {
    fetchData();
  }, [perfMode, appMode]);"""
content = content.replace(old_effect, new_effect, 1)

# 6. Propagate mode in handlers (save, delete, policy, cash)
# Global replace for holdings post endpoints
content = content.replace("fetch('http://localhost:8008/api/holdings'", "fetch(`http://localhost:8008/api/holdings?mode=${appMode === 'real' ? 'real' : 'replay'}`")

old_delete = """        const res = await fetch(`http://localhost:8008/api/holdings/${ticker}`, {
          method: 'DELETE'
        });"""
new_delete = """        const res = await fetch(`http://localhost:8008/api/holdings/${ticker}?mode=${appMode === 'real' ? 'real' : 'replay'}`, {
          method: 'DELETE'
        });"""
content = content.replace(old_delete, new_delete, 1)

old_cash = """    if (backendOnline) {
      try {
        const res = await fetch('http://localhost:8008/api/account', {
          method: 'POST',"""
new_cash = """    if (backendOnline) {
      try {
        const res = await fetch(`http://localhost:8008/api/account?mode=${appMode === 'real' ? 'real' : 'replay'}`, {
          method: 'POST',"""
content = content.replace(old_cash, new_cash, 1)

with open(filepath, "w", encoding="utf-8") as f:
    f.write(content)

print("All edits applied successfully!")
