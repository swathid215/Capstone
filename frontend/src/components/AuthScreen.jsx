import React, { useState } from 'react'
import { Eye, EyeOff, LogIn, UserPlus } from 'lucide-react'
import { apiUrl } from '../api'

const initialRegister = { username: '', phone: '', email: '', password: '', confirm_password: '' }

export default function AuthScreen({ onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [register, setRegister] = useState(initialRegister)
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const updateRegister = (field, value) => setRegister((current) => ({ ...current, [field]: value }))

  async function submit(event) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      const body = mode === 'login' ? { phone, password } : register
      const response = await fetch(apiUrl(`/api/auth/${mode}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(body),
      })
      const data = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((item) => item.msg).join(', ') : data.detail || 'Unable to authenticate')
      onAuthenticated(data.user)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const passwordInput = (
    <div className="auth-password-field">
      <input
        required
        type={showPassword ? 'text' : 'password'}
        value={mode === 'login' ? password : register.password}
        onChange={(event) => mode === 'login' ? setPassword(event.target.value) : updateRegister('password', event.target.value)}
        placeholder="Enter your password"
        autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
      />
      <button type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? 'Hide password' : 'Show password'} title={showPassword ? 'Hide password' : 'Show password'}>
        {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  )

  return (
    <main className="auth-shell">
      <section className="auth-panel">
        <div className="app-brand auth-brand">
          <div className="app-brand-mark">U</div>
          <div><div className="app-brand-name">UIDT</div><div className="app-brand-subtitle">Urban Infrastructure Digital Twin</div></div>
        </div>
        <div className="auth-heading">
          <p className="eyebrow">PROJECT WORKSPACE</p>
          <h1>{mode === 'login' ? 'Welcome back' : 'Create your account'}</h1>
          <p>{mode === 'login' ? 'Sign in to access your projects and analysis workspace.' : 'Set up a secure account for your private project workspace.'}</p>
        </div>
        <form onSubmit={submit} className="auth-form">
          {mode === 'register' && <>
            <label>Username *<small>3-40 letters, numbers, or underscores.</small><input required value={register.username} onChange={(event) => updateRegister('username', event.target.value)} autoComplete="username" /></label>
            <label>Phone number *<small>Use a reachable number with country code when needed.</small><input required value={register.phone} onChange={(event) => updateRegister('phone', event.target.value)} autoComplete="tel" /></label>
            <label>Email *<small>Used for account identification and recovery workflows.</small><input required type="email" value={register.email} onChange={(event) => updateRegister('email', event.target.value)} autoComplete="email" /></label>
          </>}
          {mode === 'login' && <label>Phone number *<small>Enter the phone number used during registration.</small><input required value={phone} onChange={(event) => setPhone(event.target.value)} autoComplete="tel" /></label>}
          <label>Password *{passwordInput}<small>Minimum 8 characters. You may use any characters.</small></label>
          {mode === 'register' && <label>Confirm password *<input required type={showPassword ? 'text' : 'password'} value={register.confirm_password} onChange={(event) => updateRegister('confirm_password', event.target.value)} autoComplete="new-password" /></label>}
          {error && <div className="auth-error" role="alert">{error}</div>}
          <button className="auth-submit" disabled={busy} type="submit">{mode === 'login' ? <LogIn size={16} /> : <UserPlus size={16} />}{busy ? 'Please wait...' : mode === 'login' ? 'Sign in' : 'Create account'}</button>
        </form>
        <button type="button" className="auth-switch" onClick={() => { setMode((current) => current === 'login' ? 'register' : 'login'); setError('') }}>
          {mode === 'login' ? 'New here? Create an account' : 'Already registered? Sign in'}
        </button>
      </section>
    </main>
  )
}
