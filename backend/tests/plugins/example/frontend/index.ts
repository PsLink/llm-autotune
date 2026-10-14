/** The example plugin's frontend: one of each contribution.
 *
 *  Copied to frontend/src/plugins/installed/example/ before `npm run build`
 *  (CI does this on every change), it adds a page with a nav entry and a card
 *  on the account page. The backend half is the `example_plugin` package next
 *  to this folder.
 */
import type { FrontendPlugin } from '@/plugins/api'

import AccountCard from './AccountCard.vue'

const plugin: FrontendPlugin = {
  name: 'example',
  routes: [{ path: '/example', component: () => import('./TicksView.vue') }],
  nav: [{ path: '/example', label: 'example.nav', group: 'top', order: 45 }],
  messages: {
    en: { example: { nav: 'Example', ticks: 'Worker ticks noted' } },
    zh: { example: { nav: '示例', ticks: '已记录的 worker 周期' } },
  },
  slots: { 'account.cards': AccountCard },
}

export default plugin
