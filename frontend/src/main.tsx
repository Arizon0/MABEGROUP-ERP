import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import App from './App'
import './index.css'

const cliente = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (falhas, erro) => {
        // 401/403/404 não melhoram com nova tentativa — só atrasam o feedback.
        const status = (erro as { status?: number })?.status
        if (status && status < 500) return false
        return falhas < 2
      },
      refetchOnWindowFocus: false,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={cliente}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
