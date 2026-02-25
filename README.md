# OIDBT IPFS Synchronizer

IPFS 同步器，自动爬取并上传数据到 IPFS

实时开发使用 `pip install -e . --config-settings editable_mode=strict`

### 删除所有 IPFS 固定

```pwsh
ipfs pin ls --type=recursive -q | ForEach-Object { ipfs pin rm $_ }
ipfs repo gc
```
