import { expect, test } from '@playwright/test'

// 后端是共享内存仓库，本文件用例会累积清单数据，强制串行避免相互干扰
test.describe.configure({ mode: 'serial' })

/**
 * V014 数据导出分片清单专项验收：
 * - 整组导出按分片独立成功/失败/取消；
 * - 工作台与详情抽屉展示相同业务数据；
 * - 批次重试只补未完成分片，不覆盖已完成文件；
 * - 单分片取消两种策略；
 * - 未清残留触发事务回滚并在 V014 留档可查；
 * - 异常检测读取清单信号编号核对告警范围。
 *
 * 后端为内存仓库且用例共享状态，所有断言都按唯一批次号定位，不依赖行序。
 */

const EXPORT_PAGE = '/data_export'
const DETECT_PAGE = '/anomaly_detect'
const API = 'http://127.0.0.1:8000/api'

async function submitDemoBatch(page: import('@playwright/test').Page, batchNo: string, taskId = 1) {
  await page.goto(EXPORT_PAGE)
  await page.getByTestId('open-batch').click()
  await expect(page.getByTestId('batch-modal')).toBeVisible()
  const modal = page.getByTestId('batch-modal')
  await modal.locator('input').nth(0).fill(batchNo)
  await modal.locator('input[type="number"]').fill('3')
  await modal.locator('select').selectOption(String(taskId))
  await page.getByTestId('submit-batch').click()
}

function rowsOfBatch(page: import('@playwright/test').Page, batchNo: string) {
  return page.locator(`[data-testid^="manifest-row-"][data-batch-no="${batchNo}"]`)
}

test('V014 整批提交：分片独立成败、工作台与详情数据一致', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`)
  })

  const batchNo = `E2E-BATCH-${Date.now()}`
  await submitDemoBatch(page, batchNo)
  await expect(page.getByTestId('batch-modal')).toHaveCount(0)

  const rows = rowsOfBatch(page, batchNo)
  await expect(rows).toHaveCount(3)
  const statuses = await rows.evaluateAll((nodes) =>
    nodes.map((n) => (n as HTMLElement).getAttribute('data-status')))
  expect(statuses).toEqual(['成功', '失败', '已取消'])

  // 全局汇总卡片至少包含本次提交的 1 成功 / 1 失败 / 1 取消
  const summaryText = await page.getByTestId('shard-summary').innerText()
  expect(summaryText).toMatch(/成功分片[\s\S]*[1-9]/)
  expect(summaryText).toMatch(/失败分片[\s\S]*[1-9]/)
  expect(summaryText).toMatch(/取消分片[\s\S]*[1-9]/)

  const firstRow = rows.nth(0)
  await expect(firstRow.getByTestId('download-shard')).toBeVisible()
  await expect(rows.nth(1).getByTestId('download-shard')).toHaveCount(0)

  // 工作台与详情抽屉是同一份业务数据
  const tableShardNo = await firstRow.locator('td').nth(0).innerText()
  const tableSignal = await firstRow.locator('td').nth(3).innerText()
  await firstRow.getByTestId('view').click()
  const detail = page.getByTestId('manifest-detail')
  await expect(detail).toBeVisible()
  await expect(detail.locator('td').filter({ hasText: tableShardNo })).toBeVisible()
  await expect(detail).toContainText(tableSignal)
  await page.getByTestId('detail-close').click()
  await expect(detail).toHaveCount(0)

  expect(errors).toEqual([])
})

test('V014 批次重试：只补未完成分片，已完成文件不覆盖', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`)
  })

  const batchNo = `E2E-RETRY-${Date.now()}`
  await submitDemoBatch(page, batchNo)
  await expect(page.getByTestId('batch-modal')).toHaveCount(0)
  const rows = rowsOfBatch(page, batchNo)
  await expect(rows).toHaveCount(3)
  const keptFileName = await rows.nth(0).locator('td').nth(6).innerText()
  expect(keptFileName).not.toBe('—')

  await page.getByTestId('open-retry').click()
  const retryModal = page.getByTestId('retry-modal')
  await expect(retryModal).toBeVisible()
  await retryModal.locator('input').fill(batchNo)
  await page.getByTestId('submit-retry').click()
  await expect(retryModal).toHaveCount(0)

  await expect(rows.nth(0)).toHaveAttribute('data-status', '成功')
  await expect(rows.nth(0).locator('td').nth(6)).toHaveText(keptFileName)
  await expect(rows.nth(1)).toHaveAttribute('data-status', '待处理')
  await expect(rows.nth(2)).toHaveAttribute('data-status', '待处理')

  expect(errors).toEqual([])
})

test('V014 单分片取消：保留成功策略与整批重试策略', async ({ page }) => {
  const batchKeep = `E2E-KEEP-${Date.now()}`
  const batchRetry = `E2E-WHOLE-${Date.now()}`

  // 策略一：保留成功分片，取消项单独记录
  await submitDemoBatch(page, batchKeep)
  await expect(page.getByTestId('batch-modal')).toHaveCount(0)
  const rows = rowsOfBatch(page, batchKeep)
  await expect(rows).toHaveCount(3)
  await rows.nth(1).getByRole('button', { name: '取消(保留成功)', exact: true }).click()
  await expect(rows.nth(0)).toHaveAttribute('data-status', '成功')
  await expect(rows.nth(1)).toHaveAttribute('data-status', '已取消')
  await expect(rows.nth(2)).toHaveAttribute('data-status', '已取消')

  // 策略二：整批转待重试，暂不发布文件，保留原分片边界
  await submitDemoBatch(page, batchRetry)
  await expect(page.getByTestId('batch-modal')).toHaveCount(0)
  const retryRows = rowsOfBatch(page, batchRetry)
  await expect(retryRows).toHaveCount(3)
  await retryRows.nth(1).getByRole('button', { name: '取消(整批重试)', exact: true }).click()
  // 整批回到待处理（含被取消的那一片），保留原分片边界且不发布文件
  for (const i of [0, 1, 2]) {
    await expect(retryRows.nth(i)).toHaveAttribute('data-status', '待处理')
  }
})

test('V014 未清残留：触发事务回滚并留档核对', async ({ page }) => {
  const batchNo = `E2E-RESIDUE-${Date.now()}`
  // 先通过规划接口制造“已规划未统一提交”的残留
  const planRes = await page.request.post(`${API}/data_export/manifests/plan`, {
    data: { task_id: 1, shard_count: 2, batch_no: batchNo },
  })
  expect(planRes.ok()).toBeTruthy()

  await page.goto(EXPORT_PAGE)
  await page.getByTestId('open-batch').click()
  const modal = page.getByTestId('batch-modal')
  await modal.locator('input').nth(0).fill(batchNo)
  await modal.locator('input[type="number"]').fill('2')
  await page.getByTestId('submit-batch').click()

  await expect(page.getByTestId('manifest-error')).toContainText('未清残留')
  // 失败后手工关闭弹窗，再打开留档
  await modal.locator('button', { hasText: '取消' }).click()
  await expect(modal).toHaveCount(0)

  await page.getByTestId('open-archive').click()
  const archiveModal = page.getByTestId('archive-modal')
  await expect(archiveModal).toBeVisible()
  await expect(archiveModal).toContainText('residue_rollback')
  await expect(archiveModal).toContainText('V014')
})

test('V014 跨模块：异常检测读取清单信号编号核对告警范围', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`)
  })

  const batchNo = `E2E-RECON-${Date.now()}`
  await submitDemoBatch(page, batchNo)
  await expect(page.getByTestId('batch-modal')).toHaveCount(0)

  await page.goto(DETECT_PAGE)
  await expect(page.getByTestId('v014-panel')).toBeVisible()
  await expect(page.locator('[data-testid^="signal-row-"]').first()).toBeVisible()

  await page.getByTestId('reconcile-task').fill('1')
  await page.getByTestId('reconcile-batch').fill(batchNo)
  await page.getByTestId('run-reconcile').click()

  const result = page.getByTestId('reconcile-result')
  await expect(result).toBeVisible()
  await expect(result.locator('[data-testid^="reconcile-row-"]')).toHaveCount(3)
  await expect(result).toContainText('核对一致')
  await expect(page.getByTestId('v014-error')).toHaveCount(0)

  expect(errors).toEqual([])
})
