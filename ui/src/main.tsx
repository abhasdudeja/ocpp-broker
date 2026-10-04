import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { AppRoutes } from './App'
import { AuthProvider } from './auth'
import './styles.css'

const root = document.getElementById('root')
if (!root) throw new Error('index.html has no #root element')

createRoot(root).render(
  <StrictMode>
    <AuthProvider>
      {/* The broker serves the console under /ui */}
      <BrowserRouter basename="/ui">
        <AppRoutes />
      </BrowserRouter>
    </AuthProvider>
  </StrictMode>,
)
