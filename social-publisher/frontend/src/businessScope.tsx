import { createContext, useContext, type ReactNode } from 'react'

type BusinessScopeValue = {
  businessId: string
}

const BusinessScopeContext = createContext<BusinessScopeValue | null>(null)

export function BusinessScopeProvider({
  businessId,
  children,
}: {
  businessId: string
  children: ReactNode
}) {
  return (
    <BusinessScopeContext.Provider value={{ businessId }}>
      {children}
    </BusinessScopeContext.Provider>
  )
}

export function useBusinessScope() {
  const value = useContext(BusinessScopeContext)
  if (!value) throw new Error('useBusinessScope must be used inside BusinessScopeProvider')
  return value
}
