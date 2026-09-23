{{- if .Values.app.autoscaling.enabled }}
{{- if not .Values.app.metrics.enabled }}
{{- fail "app.metrics.enabled must be true when app.autoscaling.enabled is true" }}
{{- end }}
{{- if gt (int .Values.app.autoscaling.minReplicaCount) (int .Values.app.autoscaling.maxReplicaCount) }}
{{- fail "app.autoscaling.minReplicaCount must not exceed maxReplicaCount" }}
{{- end }}
{{- if or (lt (int .Values.app.autoscaling.fallback.replicas) (int .Values.app.autoscaling.minReplicaCount)) (gt (int .Values.app.autoscaling.fallback.replicas) (int .Values.app.autoscaling.maxReplicaCount)) }}
{{- fail "app.autoscaling.fallback.replicas must be within the configured replica range" }}
{{- end }}
{{- $appName := include "typescale.appFullname" . }}
{{- $podRegex := printf "%s-.+" $appName }}
{{- $jobName := .Values.app.autoscaling.prometheus.jobName }}
{{- $defaultQuery := printf "sum(typescale_websocket_connections{namespace=%q,job=%q,pod=~%q} and on(namespace,job,pod) (up{namespace=%q,job=%q,pod=~%q} == 1))" .Release.Namespace $jobName $podRegex .Release.Namespace $jobName $podRegex }}
{{- $query := .Values.app.autoscaling.prometheus.query | default $defaultQuery }}
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: {{ include "typescale.appFullname" . }}
  labels:
    {{- include "typescale.labels" . | nindent 4 }}
    app.kubernetes.io/component: application
spec:
  scaleTargetRef:
    name: {{ include "typescale.appFullname" . }}
  pollingInterval: {{ .Values.app.autoscaling.pollingInterval }}
  cooldownPeriod: {{ .Values.app.autoscaling.cooldownPeriod }}
  minReplicaCount: {{ .Values.app.autoscaling.minReplicaCount }}
  maxReplicaCount: {{ .Values.app.autoscaling.maxReplicaCount }}
  fallback:
    failureThreshold: {{ .Values.app.autoscaling.fallback.failureThreshold }}
    replicas: {{ .Values.app.autoscaling.fallback.replicas }}
    behavior: {{ .Values.app.autoscaling.fallback.behavior }}
  advanced:
    restoreToOriginalReplicaCount: false
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:
          stabilizationWindowSeconds: 0
          policies:
            - type: Pods
              value: 3
              periodSeconds: 15
            - type: Percent
              value: 100
              periodSeconds: 15
          selectPolicy: Max
        scaleDown:
          stabilizationWindowSeconds: 180
          policies:
            - type: Pods
              value: 1
              periodSeconds: 60
          selectPolicy: Max
  triggers:
    - type: prometheus
      metricType: AverageValue
      useCachedMetrics: true
      metadata:
        serverAddress: {{ .Values.app.autoscaling.prometheus.serverAddress | quote }}
        metricName: typescale_active_websocket_connections
        query: {{ $query | quote }}
        threshold: {{ .Values.app.autoscaling.prometheus.threshold | quote }}
        activationThreshold: {{ .Values.app.autoscaling.prometheus.activationThreshold | quote }}
        ignoreNullValues: "false"
{{- end }}
