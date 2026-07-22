import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useSites } from '@/hooks/useData'
import type { Site } from '@/types/domain'

type BusinessScopeValue = {
  businessId: string
  businessIds: string[]
  sites: Site[]
  businessSites: Site[]
  unassignedSites: Site[]
  loading: boolean
  setBusinessId: (businessId: string) => void
  refresh: () => void
}

const BusinessScopeContext = createContext<BusinessScopeValue | null>(null)

export function BusinessScopeProvider({ children }: { children: ReactNode }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const siteState = useSites(refreshKey)
  const sites = siteState.data ?? []
  const businessIds = useMemo(
    () => Array.from(new Set(sites.map((site) => site.business_id).filter((value): value is string => Boolean(value)))).sort(),
    [sites],
  )
  const [businessId, setBusinessId] = useState('')

  useEffect(() => {
    if (!businessIds.length) {
      setBusinessId('')
      return
    }
    if (!businessIds.includes(businessId)) setBusinessId(businessIds[0])
  }, [businessId, businessIds])

  const value = useMemo<BusinessScopeValue>(() => ({
    businessId,
    businessIds,
    sites,
    businessSites: businessId ? sites.filter((site) => site.business_id === businessId) : [],
    unassignedSites: sites.filter((site) => !site.business_id),
    loading: siteState.loading,
    setBusinessId,
    refresh: () => setRefreshKey((key) => key + 1),
  }), [businessId, businessIds, sites, siteState.loading])
  return <BusinessScopeContext.Provider value={value}>{children}</BusinessScopeContext.Provider>
}

export function useBusinessScope() {
  const value = useContext(BusinessScopeContext)
  if (!value) throw new Error('useBusinessScope must be used inside BusinessScopeProvider')
  return value
}
