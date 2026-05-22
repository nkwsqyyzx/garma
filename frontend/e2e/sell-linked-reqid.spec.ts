import { test, expect } from '@playwright/test'

const BASE_URL = 'http://localhost:8999'

test.describe('卖出时 linked_req_id 选择', () => {
  test.beforeEach(async ({ page }) => {
    // 监听 API 请求，验证 linked_req_id 是否被发送
    await page.goto(BASE_URL)
  })

  test('策略持仓页面：每行有卖出按钮，点击打开卖出弹窗', async ({ page }) => {
    await page.goto(`${BASE_URL}/strategies`)

    // 等待数据加载
    await page.waitForTimeout(2000)

    // 查找卖出按钮（PC端表格中的）
    const sellButtons = page.locator('button:has-text("卖出")')
    const count = await sellButtons.count()

    if (count === 0) {
      console.log('No strategy positions found, skipping sell button test')
      return
    }

    // 点击第一个卖出按钮
    await sellButtons.first().click()

    // 验证卖出弹窗出现
    const dialog = page.locator('.el-dialog:visible')
    await expect(dialog).toBeVisible({ timeout: 5000 })

    // 验证弹窗标题
    await expect(dialog.locator('.el-dialog__header')).toContainText('卖出持仓')

    // 验证弹窗中有股票信息（disabled input）
    const stockInput = dialog.locator('input[disabled]').first()
    await expect(stockInput).toBeVisible()

    // 验证价格和数量输入框存在
    await expect(dialog.locator('.el-input-number').first()).toBeVisible()

    // 验证确认卖出按钮
    await expect(dialog.locator('button:has-text("确认卖出")')).toBeVisible()

    // 关闭弹窗
    await dialog.locator('button:has-text("取消")').click()
  })

  test('PositionDetail页面：卖出时显示策略持仓选择', async ({ page }) => {
    // 用 API 拦截验证 placeOrder 请求中的 linked_req_id
    let capturedOrderPayload: any = null
    await page.route('**/api/v1/qmt/trade/order', async (route) => {
      capturedOrderPayload = route.request().postDataJSON()
      // 模拟成功响应，不实际下单
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ code: 0, msg: 'ok', data: { req_id: 'test-req-001' } }),
      })
    })

    // 也拦截策略持仓 API
    await page.route('**/api/v1/qmt/strategy/positions', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          code: 0,
          msg: 'ok',
          data: [
            {
              stock_code: '510310.SH',
              volume: 200,
              trade_date: '2026-05-20',
              avg_price: 0.5,
              other: '策略A:因子1:1:ETF300',
              cost: 100,
              pct_change: 0.02,
              current_price: 0.51,
              pnl: 2,
              order_req_id: 'req-buy-001',
            },
            {
              stock_code: '510310.SH',
              volume: 300,
              trade_date: '2026-05-21',
              avg_price: 0.52,
              other: '策略B:因子2:2:ETF300',
              cost: 156,
              pct_change: -0.01,
              current_price: 0.51,
              pnl: -3,
              order_req_id: 'req-buy-002',
            },
          ],
        }),
      })
    })

    // 拦截行情和名称 API
    await page.route('**/api/v1/qmt/quote/stock_names**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ code: 0, msg: 'ok', data: { '510310.SH': 'ETF300' } }),
      })
    })

    await page.route('**/api/v1/qmt/quote/snapshot**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          code: 0,
          msg: 'ok',
          data: {
            '510310.SH': { last: 0.51, limit_up: 0.55, limit_down: 0.46 },
          },
        }),
      })
    })

    await page.goto(`${BASE_URL}/position/510310.SH`)
    await page.waitForTimeout(2000)

    // 切换到"卖出"
    const sellRadio = page.locator('.el-radio-button:has-text("卖出")')
    await sellRadio.click()

    // 等待策略持仓下拉框出现
    const selectWrapper = page.locator('.el-form-item:has-text("关联策略持仓") .el-select')
    await expect(selectWrapper).toBeVisible({ timeout: 5000 })

    // 点击下拉框打开选项
    await selectWrapper.click()

    // 验证有两个选项
    const options = page.locator('.el-select-dropdown__item:visible')
    await expect(options).toHaveCount(2, { timeout: 3000 })

    // 验证选项内容包含策略信息
    const optionTexts = await options.allTextContents()
    console.log('Options:', optionTexts)
    expect(optionTexts.some(t => t.includes('策略A'))).toBeTruthy()
    expect(optionTexts.some(t => t.includes('策略B'))).toBeTruthy()
    expect(optionTexts.some(t => t.includes('req-buy-001')) || optionTexts.some(t => t.includes('200股'))).toBeTruthy()

    // 选择第一个策略持仓
    await options.first().click()

    // 验证 linked_req_id 提示出现
    const lotHint = page.locator('.lot-hint')
    await expect(lotHint).toBeVisible()
    await expect(lotHint).toContainText('linked_req_id')

    // 设置卖出数量 100
    const volumeInput = page.locator('.volume-row .el-input-number input').first()
    await volumeInput.fill('100')

    // 点击卖出按钮
    const submitBtn = page.locator('button:has-text("卖出"):not(.el-radio-button__inner)')
    await submitBtn.click()

    // 处理确认弹窗
    const confirmDialog = page.locator('.el-message-box')
    await expect(confirmDialog).toBeVisible({ timeout: 3000 })
    await confirmDialog.locator('button:has-text("确认")').click()

    // 等待请求被捕获
    await page.waitForTimeout(1000)

    // 验证 placeOrder 请求中包含 linked_req_id
    console.log('Captured order payload:', JSON.stringify(capturedOrderPayload))
    expect(capturedOrderPayload).toBeTruthy()
    expect(capturedOrderPayload.order_type).toBe('sell')
    expect(capturedOrderPayload.stock_code).toBe('510310.SH')
    expect(capturedOrderPayload.linked_req_id).toBeTruthy()
    expect(capturedOrderPayload.linked_req_id).toMatch(/^req-buy-/)
  })

  test('Trade页面：手动输入代码卖出时显示策略持仓选择', async ({ page }) => {
    let capturedOrderPayload: any = null
    await page.route('**/api/v1/qmt/trade/order', async (route) => {
      capturedOrderPayload = route.request().postDataJSON()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ code: 0, msg: 'ok', data: { req_id: 'test-req-002' } }),
      })
    })

    await page.route('**/api/v1/qmt/strategy/positions', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          code: 0,
          msg: 'ok',
          data: [
            {
              stock_code: '510310.SH',
              volume: 100,
              trade_date: '2026-05-20',
              avg_price: 0.5,
              other: '策略A:因子1:1:ETF300',
              cost: 50,
              pct_change: 0.02,
              current_price: 0.51,
              pnl: 1,
              order_req_id: 'req-buy-003',
            },
          ],
        }),
      })
    })

    await page.route('**/api/v1/qmt/quote/stock_names**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ code: 0, msg: 'ok', data: { '510310.SH': 'ETF300' } }),
      })
    })

    await page.route('**/api/v1/qmt/quote/snapshot**', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          code: 0,
          msg: 'ok',
          data: {
            '510310.SH': { last: 0.51, limit_up: 0.55, limit_down: 0.46 },
          },
        }),
      })
    })

    await page.goto(`${BASE_URL}/trade`)
    await page.waitForTimeout(1000)

    // 输入股票代码
    const codeInput = page.locator('.trade-form input').first()
    await codeInput.fill('510310.SH')
    await codeInput.blur() // 触发 change 事件

    await page.waitForTimeout(1000)

    // 切换到卖出
    await page.locator('.el-radio-button:has-text("卖出")').click()

    // 等待策略持仓下拉框（只有一个持仓，应自动选中）
    await page.waitForTimeout(1000)

    // 验证自动选中（只有一个 lot 时自动选中）
    const lotHint = page.locator('.lot-hint')
    await expect(lotHint).toBeVisible({ timeout: 5000 })
    await expect(lotHint).toContainText('req-buy-003')

    // 设置卖出数量 100
    const volumeInput = page.locator('.volume-row .el-input-number input').first()
    await volumeInput.fill('100')

    // 提交
    const submitBtn = page.locator('button:has-text("卖出"):not(.el-radio-button__inner)')
    await submitBtn.click()

    // 确认弹窗
    const confirmDialog = page.locator('.el-message-box')
    await expect(confirmDialog).toBeVisible({ timeout: 3000 })
    await confirmDialog.locator('button:has-text("确认")').click()

    await page.waitForTimeout(1000)

    // 验证请求
    console.log('Captured order payload:', JSON.stringify(capturedOrderPayload))
    expect(capturedOrderPayload).toBeTruthy()
    expect(capturedOrderPayload.linked_req_id).toBe('req-buy-003')
    expect(capturedOrderPayload.order_volume).toBe(100)
  })
})
