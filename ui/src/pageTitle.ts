const TITLES: Array<[RegExp, string]> = [
  [/^\/signin/, 'Sign in'],
  [/^\/chargers\/[^/]+\/[^/]+/, 'Charger'],
  [/^\/chargers/, 'Chargers'],
  [/^\/backends/, 'Backends'],
  [/^\/history\/transactions/, 'Transaction'],
  [/^\/history/, 'History'],
  [/^\/tags/, 'Tags'],
  [/^\/admin\/new/, 'Add an organization'],
  [/^\/admin\/orgs/, 'Organization'],
  [/^\/admin/, 'Admin'],
]

/** What the browser tab and a screen reader's page-change announcement should call the page at ``pathname``. */
export function pageTitle(pathname: string): string {
  const found = TITLES.find(([pattern]) => pattern.test(pathname))
  return found ? `${found[1]} · OCPP Broker` : 'Overview · OCPP Broker'
}
