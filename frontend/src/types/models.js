/**
 * @typedef {'critical' | 'high' | 'medium' | 'low' | 'info'} Severity
 */

/**
 * @typedef {'open' | 'reviewing' | 'resolved'} FindingStatus
 */

/**
 * @typedef {Object} SummaryMetric
 * @property {string} id
 * @property {string} label
 * @property {number|string} value
 * @property {string} helper
 * @property {string} tone
 */

/**
 * @typedef {Object} SeverityCount
 * @property {Severity} severity
 * @property {string} label
 * @property {number} count
 * @property {string} color
 */

/**
 * @typedef {Object} Vulnerability
 * @property {string} id
 * @property {string} title
 * @property {string} category
 * @property {Severity} severity
 * @property {number} cvss
 * @property {FindingStatus} status
 * @property {string} endpoint
 * @property {string} method
 * @property {string} detectedAt
 * @property {string} summary
 * @property {string} evidence
 * @property {string} impact
 * @property {string[]} remediation
 * @property {string[]} references
 */

/**
 * @typedef {'role' | 'page' | 'endpoint' | 'resource'} GraphNodeType
 */

/**
 * @typedef {Object} GraphNode
 * @property {string} id
 * @property {string} label
 * @property {GraphNodeType} type
 * @property {number} x
 * @property {number} y
 * @property {Severity=} severity
 */

/**
 * @typedef {Object} GraphLink
 * @property {string} source
 * @property {string} target
 * @property {string} relation
 */

/**
 * @typedef {Object} Activity
 * @property {string} id
 * @property {string} title
 * @property {string} description
 * @property {string} time
 * @property {'scan'|'finding'|'resolved'} type
 */

/**
 * @typedef {Object} ProjectSummary
 * @property {string} name
 * @property {string} targetUrl
 * @property {string} lastScan
 * @property {string} scanDuration
 * @property {number} securityScore
 */

export {};
