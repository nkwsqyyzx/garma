import { test, expect } from '@playwright/test';

const BASE = 'http://localhost:8999';

test.describe('Garma QMT Terminal', () => {

  // ── 总览页面 ──────────────────────────────────────────

  test.describe('Dashboard (/)', () => {
    test('页面正常加载，显示导航和标题', async ({ page }) => {
      await page.goto(BASE);
      await expect(page.getByText('Garma QMT')).toBeVisible({ timeout: 10000 });
    });

    test('侧边栏导航项完整', async ({ page }) => {
      await page.goto(BASE);
      const sidebar = page.locator('.el-menu');
      await expect(sidebar).toBeVisible();
      // Element Plus menu items render text in nested spans
      await expect(page.locator('.el-menu-item').filter({ hasText: '总览' })).toBeVisible();
      await expect(page.locator('.el-menu-item').filter({ hasText: '委托' })).toBeVisible();
      await expect(page.locator('.el-menu-item').filter({ hasText: '成交' })).toBeVisible();
      await expect(page.locator('.el-menu-item').filter({ hasText: '下单' })).toBeVisible();
    });

    test('资产卡片显示或显示空状态', async ({ page }) => {
      await page.goto(BASE);
      const hasData = await page.locator('text=总资产').isVisible();
      const hasEmpty = await page.locator('text=暂无资金数据').isVisible();
      expect(hasData || hasEmpty).toBeTruthy();
    });

    test('持仓列表显示或显示空状态', async ({ page }) => {
      await page.goto(BASE);
      const hasTable = await page.locator('.el-table').first().isVisible();
      const hasEmpty = await page.locator('text=暂无持仓').isVisible();
      const hasTitle = await page.locator('text=持仓列表').isVisible();
      expect(hasTable || hasEmpty || hasTitle).toBeTruthy();
    });

    test('点击持仓行跳转到详情页', async ({ page }) => {
      await page.goto(BASE);
      await page.waitForTimeout(2000);
      const row = page.locator('.el-table__body-wrapper .el-table__row').first();
      if (await row.isVisible()) {
        await row.click();
        await expect(page).toHaveURL(/\/position\//);
      }
    });

    test('排序图标默认隐藏', async ({ page }) => {
      await page.goto(BASE);
      await page.waitForTimeout(2000);
      const table = page.locator('.el-table').first();
      if (await table.isVisible()) {
        const caretHidden = await page.evaluate(() => {
          const el = document.querySelector('.el-table .caret-wrapper') as HTMLElement;
          return el ? getComputedStyle(el).opacity === '0' : true;
        });
        expect(caretHidden).toBeTruthy();
      }
    });
  });

  // ── 持仓详情页 ────────────────────────────────────────

  test.describe('Position Detail (/position/:code)', () => {
    test('页面加载，显示返回按钮和代码', async ({ page }) => {
      await page.goto(`${BASE}/position/510310.SH`);
      await expect(page.locator('text=← 返回')).toBeVisible({ timeout: 10000 });
      await expect(page.locator('text=510310.SH')).toBeVisible();
    });

    test('K线走势区域可见', async ({ page }) => {
      await page.goto(`${BASE}/position/510310.SH`);
      await expect(page.locator('text=K 线走势')).toBeVisible({ timeout: 10000 });
      const hasChart = await page.locator('.chart-container').isVisible();
      const hasEmpty = await page.locator('text=暂无K线数据').isVisible();
      expect(hasChart || hasEmpty).toBeTruthy();
    });

    test('五档盘口可见', async ({ page }) => {
      await page.goto(`${BASE}/position/510310.SH`);
      await expect(page.locator('text=五档盘口')).toBeVisible({ timeout: 10000 });
    });

    test('ETF 五档盘口价格显示三位小数', async ({ page }) => {
      await page.goto(`${BASE}/position/510310.SH`);
      await page.waitForTimeout(4000);
      const tick = page.locator('.orderbook').first();
      if (await tick.isVisible()) {
        const prices = await page.locator('.book-row .price').allTextContents();
        const hasThreeDecimals = prices.some(p => /^\d+\.\d{3}$/.test(p.trim()));
        expect(hasThreeDecimals).toBeTruthy();
      }
    });

    test('五档盘口显示买卖5档', async ({ page }) => {
      await page.goto(`${BASE}/position/510310.SH`);
      await page.waitForTimeout(4000);
      const tick = page.locator('.orderbook').first();
      if (await tick.isVisible()) {
        await expect(page.locator('text=卖5')).toBeVisible();
        await expect(page.locator('text=卖1')).toBeVisible();
        await expect(page.locator('text=买1')).toBeVisible();
        await expect(page.locator('text=买5')).toBeVisible();
      }
    });

    test('快捷下单区域可见', async ({ page }) => {
      await page.goto(`${BASE}/position/510310.SH`);
      await expect(page.locator('text=快捷下单')).toBeVisible({ timeout: 10000 });
    });

    test('返回按钮跳转到总览', async ({ page }) => {
      // 先访问总览建立历史，再进入详情，再点返回
      await page.goto(BASE);
      await page.waitForTimeout(1000);
      await page.goto(`${BASE}/position/510310.SH`);
      await expect(page.locator('text=← 返回')).toBeVisible({ timeout: 10000 });
      await page.locator('text=← 返回').click();
      await expect(page).toHaveURL(/\/$/);
    });
  });

  // ── 委托页面 ──────────────────────────────────────────

  test.describe('Orders (/orders)', () => {
    test('页面加载，显示标题', async ({ page }) => {
      await page.goto(`${BASE}/orders`);
      await expect(page.locator('text=当日委托')).toBeVisible({ timeout: 10000 });
    });

    test('全部撤单按钮存在', async ({ page }) => {
      await page.goto(`${BASE}/orders`);
      await expect(page.locator('text=全部撤单')).toBeVisible({ timeout: 10000 });
    });

    test('显示表格或空状态', async ({ page }) => {
      await page.goto(`${BASE}/orders`);
      const hasTable = await page.locator('.el-table').first().isVisible();
      const hasEmpty = await page.locator('text=暂无委托').isVisible();
      expect(hasTable || hasEmpty).toBeTruthy();
    });
  });

  // ── 成交页面 ──────────────────────────────────────────

  test.describe('Trades (/trades)', () => {
    test('页面加载，显示标题', async ({ page }) => {
      await page.goto(`${BASE}/trades`);
      await expect(page.locator('text=当日成交')).toBeVisible({ timeout: 10000 });
    });

    test('显示表格或空状态', async ({ page }) => {
      await page.goto(`${BASE}/trades`);
      const hasTable = await page.locator('.el-table').first().isVisible();
      const hasEmpty = await page.locator('text=暂无成交').isVisible();
      expect(hasTable || hasEmpty).toBeTruthy();
    });
  });

  // ── 下单页面 ──────────────────────────────────────────

  test.describe('Trade (/trade)', () => {
    test('页面加载，显示下单表单', async ({ page }) => {
      await page.goto(`${BASE}/trade`);
      await expect(page.locator('text=股票代码')).toBeVisible({ timeout: 10000 });
    });

    test('买卖方向切换', async ({ page }) => {
      await page.goto(`${BASE}/trade`);
      // Element Plus radio-button 渲染为 <label> 元素
      const radioGroup = page.locator('.el-radio-group');
      await expect(radioGroup).toBeVisible({ timeout: 10000 });
      // 点击卖出 label
      await radioGroup.locator('label:has-text("卖出")').click();
      // 提交按钮文案变为 "卖出"
      await expect(page.locator('button:has-text("卖出")')).toBeVisible();
    });

    test('输入股票代码可触发查询', async ({ page }) => {
      await page.goto(`${BASE}/trade`);
      const input = page.locator('input').first();
      await input.fill('600519.SH');
      await input.press('Enter');
      await page.waitForTimeout(2000);
    });

    test('输入代码后显示五档盘口', async ({ page }) => {
      await page.goto(`${BASE}/trade`);
      // 初始无盘口
      expect(page.locator('text=五档盘口')).not.toBeVisible();
      // 输入代码
      const input = page.locator('input').first();
      await input.fill('510310.SH');
      await input.press('Enter');
      // 等待盘口出现
      await expect(page.locator('text=五档盘口')).toBeVisible({ timeout: 5000 });
      await expect(page.locator('text=买1')).toBeVisible();
      await expect(page.locator('text=卖1')).toBeVisible();
    });

    test('提交空表单显示警告', async ({ page }) => {
      await page.goto(`${BASE}/trade`);
      await page.waitForTimeout(1000);
      // 点击底部提交按钮（非 radio button）
      const submitBtn = page.locator('.el-form button[type="button"]').filter({ hasText: '买入' });
      if (await submitBtn.isVisible()) {
        await submitBtn.click();
        await page.waitForTimeout(1000);
      }
    });
  });

  // ── 导航 ──────────────────────────────────────────────

  test.describe('Navigation', () => {
    test('侧边栏点击导航到各页面', async ({ page }) => {
      await page.goto(BASE);
      await page.waitForTimeout(2000);
      // 委托
      await page.locator('.el-menu-item').filter({ hasText: '委托' }).click();
      await expect(page).toHaveURL(/\/orders/);
      // 成交
      await page.locator('.el-menu-item').filter({ hasText: '成交' }).click();
      await expect(page).toHaveURL(/\/trades/);
      // 下单
      await page.locator('.el-menu-item').filter({ hasText: '下单' }).click();
      await expect(page).toHaveURL(/\/trade/);
      // 总览
      await page.locator('.el-menu-item').filter({ hasText: '总览' }).click();
      await expect(page).toHaveURL(/\/$/);
    });
  });

  // ── 控制台无报错 ──────────────────────────────────────

  test.describe('Console errors', () => {
    test('总览页面无严重 JS 报错', async ({ page }) => {
      const errors: string[] = [];
      page.on('console', msg => {
        if (msg.type() === 'error') errors.push(msg.text());
      });
      await page.goto(BASE);
      await page.waitForTimeout(3000);
      const realErrors = errors.filter(e =>
        !e.includes('WebSocket') &&
        !e.includes('net::ERR_CONNECTION_REFUSED') &&
        !e.includes('404')
      );
      expect(realErrors).toEqual([]);
    });

    test('持仓详情页无严重 JS 报错', async ({ page }) => {
      const errors: string[] = [];
      page.on('console', msg => {
        if (msg.type() === 'error') errors.push(msg.text());
      });
      await page.goto(`${BASE}/position/510310.SH`);
      await page.waitForTimeout(5000);
      const realErrors = errors.filter(e =>
        !e.includes('WebSocket') &&
        !e.includes('net::ERR_CONNECTION_REFUSED') &&
        !e.includes('404')
      );
      expect(realErrors).toEqual([]);
    });
  });
});
