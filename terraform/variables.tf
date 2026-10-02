variable "prefix" {
  description = "Prefix for all resource names"
  type        = string
  default     = "sre"
}

variable "location" {
  description = "Azure region for the cluster"
  type        = string
  default     = "centralindia"
}

variable "kubernetes_version" {
  description = "AKS Kubernetes version (null = latest available)"
  type        = string
  default     = null
}

variable "node_vm_size" {
  description = "VM size for the system node pool"
  type        = string
  default     = "Standard_D2s_v3"
}

variable "node_count" {
  description = "Node count for the system node pool"
  type        = number
  default     = 2
}
